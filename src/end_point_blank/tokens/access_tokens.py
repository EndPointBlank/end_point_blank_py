from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from .token_result import TokenOutcome, TokenResult

logger = logging.getLogger(__name__)

_REFRESH_BUFFER = timedelta(minutes=2)
_MIN_TTL = timedelta(seconds=30)


class AccessTokens:
    """
    Thread-safe singleton holding this process's access tokens, one per
    application environment.

    A token is cached under the canonical base URL intake resolved the request
    to — not under the URL the caller supplied. A caller asks for the URL it is
    about to call; intake answers with the base URL of the environment that URL
    belongs to, and subsequent calls anywhere under that base URL reuse the
    entry.

    Lookup is a plain exact-or-path-prefix comparison, with the longest match
    winning. The SDK deliberately does not normalize: intake owns that rule, and
    a miss costs one extra request rather than a wrong answer.

    A lookup has to scan the keys, and the fast path deliberately does not take
    the lock, so every write **replaces** the map instead of mutating it. A
    reader then takes one atomic read of ``_entries`` and iterates something
    nobody can change underneath it. Mutating in place would raise
    ``RuntimeError: dictionary changed size during iteration`` as soon as one
    thread minted a token for a second target while another was doing a lookup.

    Tokens are automatically refreshed when they are within 2 minutes of expiry.

    Why a mint failed is kept alongside the tokens and read back with
    :meth:`last_failure`. ``token`` still answers ``None`` for every failure --
    that contract is published -- so the reason has to be queryable separately
    or a caller cannot tell a credential that must be re-issued from an intake
    that is briefly down. The failure map follows the same copy-on-write rule as
    ``_entries``, and for the same reason: :meth:`last_failure` reads it without
    the lock.

    Equivalent to the Ruby gem's ``EndPointBlank::AccessTokens``.
    """

    _instance: Optional["AccessTokens"] = None
    _init_lock = threading.Lock()

    # DO NOT REMOVE THIS BOUND. Failures are keyed on the URL the caller asked
    # about, which -- unlike a minted entry's canonical key -- a caller can
    # invent without limit. The failure mode is the one this whole mechanism
    # exists for: with a revoked credential every mint fails indefinitely, so a
    # service walking /orders/1, /orders/2, /orders/3 ... records a new entry
    # per URL forever, and the only thing that clears a record is a successful
    # mint that is never coming. In a long-lived process, inside an SDK
    # embedded in a customer's application, that is an unbounded leak -- the
    # exact trap ``_entries`` avoids by keying on what intake resolved to.
    #
    # So the cap is enforced on insert, not on some later success, and the
    # oldest record goes to make room. This is a diagnostic, not a cache;
    # losing the oldest costs nothing.
    _FAILURE_CAP = 64

    def __new__(cls) -> "AccessTokens":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._entries: dict = {}
                    instance._failures: dict = {}
                    instance._lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    def token(self, base_url: str) -> Optional[str]:
        """
        Returns a valid access token for *base_url*, fetching one if no usable
        entry covers it.

        :param base_url: The URL you are about to call, with any query string
            and fragment removed. It is sent verbatim; intake normalizes it and
            matches it against registered base URLs by longest path prefix.
        :returns: The access token string, or ``None`` if generation failed --
            which includes a response that carried a token but no ``base_url``.
            ``None`` says nothing about *why*; call :meth:`last_failure` to find
            out whether the credential was rejected or intake was simply down.
        """
        entry = self._match(base_url)
        if self._usable(entry):
            return entry["token"]

        with self._lock:
            # Another caller may have filled it while this one waited.
            entry = self._match(base_url)
            if self._usable(entry):
                return entry["token"]

            # The entry (if any) this request is refreshing. Captured once,
            # before the mint, and reused by both outcomes below: the failure
            # path deletes it outright, and the success path deletes it too
            # when intake's answer resolves to a different canonical key --
            # otherwise the stale entry just matched would survive alongside
            # the new one and, being the longer/older key, could keep winning
            # the match forever.
            matched_key = self._match_key(base_url, self._entries)

            from ..commands.generate_access_token import GenerateAccessToken
            result = GenerateAccessToken.token_result(base_url)
            payload = result.payload

            if result.outcome is TokenOutcome.SUCCESS:
                # SUCCESS is only ever a 2xx carrying both a token and the
                # canonical base URL intake resolved to; GenerateAccessToken
                # downgrades anything else to SERVER_ERROR. So both are safe to
                # read here, and a non-2xx body that merely looks like a token
                # response cannot reach this branch -- storing one would present
                # a credential intake had just refused.
                #
                # The key is what intake resolved to, and only that. There is no
                # fallback to the requested URL: that would key on the resource
                # the caller happened to ask about, so a service walking
                # /orders/1, /orders/2, /orders/3 would mint and store a token
                # per resource, and nothing here evicts.
                key = payload["base_url"]
                entries = self._entries
                if matched_key is not None and matched_key != key:
                    entries = {k: v for k, v in entries.items() if k != matched_key}
                self._entries = {
                    **entries,
                    key: {
                        "token": payload["token"],
                        "expired_at": self._parse_expiry(payload.get("expired_at")),
                    },
                }
                self._forget_failures(base_url, key)
                return payload["token"]

            # A failed refresh must not leave an expiring token behind claiming
            # to be usable — callers would keep presenting it right up to the
            # 401. Only the entry that covers this URL goes: the longest match
            # is the one that was just found unusable, so a shorter, still-good
            # entry survives.
            stale = matched_key
            if stale is not None:
                self._entries = {k: v for k, v in self._entries.items() if k != stale}

            self._record_failure(base_url, result)

            if result.outcome is TokenOutcome.CREDENTIAL_REJECTED:
                # Loud and separate on purpose. Every other failure here is
                # something to wait out; this one is not, and a line that reads
                # like the others buries the only case an operator has to act
                # on. Retrying is not suppressed -- that is a separate decision
                # a caller makes off ``last_failure`` -- but the log must not
                # pretend the retries are going anywhere.
                logger.error(
                    "EndPointBlank rejected the credential while generating an access token "
                    "for %s (HTTP 401). Retrying will not help: the client_id/client_secret "
                    "is invalid or revoked and must be re-issued.",
                    base_url,
                )
            else:
                logger.error(
                    "Failed to generate access token for %s: %s",
                    base_url,
                    self._failure_reason(result),
                )
            return None

    def exists(self, base_url: str) -> bool:
        """Returns ``True`` if a token covering *base_url* has 30+ seconds left."""
        entry = self._match(base_url)
        return bool(entry and entry["expired_at"] > datetime.now(tz=timezone.utc) + _MIN_TTL)

    def last_failure(self, base_url: str) -> Optional[TokenResult]:
        """
        Returns the most recent mint failure covering *base_url*, or ``None``.

        ``token`` answers ``None`` for every kind of failure, so this is how a
        caller finds out which kind it was::

            if tokens.token(url) is None:
                failure = tokens.last_failure(url)
                if failure and not failure.retryable:
                    ...  # a 401 needs a new credential; a 422 needs the
                         # environment registered. Backing off achieves nothing.

        A record is written whenever a mint hands back no usable token -- which
        includes a 2xx that carried no ``token`` or no ``base_url``, recorded
        with :attr:`~end_point_blank.tokens.token_result.TokenOutcome.SUCCESS`
        so a broken server is not mistaken for a rejected one. It is dropped as
        soon as a mint covering the same URL works, and by :meth:`clear`.

        Matching is the same exact-or-path-prefix rule ``token`` uses, so the
        URL a caller is about to retry finds the failure recorded for the URL it
        tried before.

        Scoped per target, not per process: a 401 for one base URL says nothing
        about another, since a process can hold tokens for several environments.
        Only the most recent ``_FAILURE_CAP`` targets are kept -- see the cap.

        :param base_url: The URL you asked for a token for.
        :returns: The recorded
            :class:`~end_point_blank.tokens.token_result.TokenResult`, or
            ``None`` if nothing covering *base_url* has failed since the last
            success.
        """
        failures = self._failures  # One atomic read; writes replace, never mutate.
        key = self._match_key(base_url, failures)
        return failures.get(key) if key is not None else None

    def invalidate(self, stale_token: Optional[str]) -> None:
        """
        Discards a held token, but only if it is still the one the caller had.

        Every request in flight when a token is rejected reports the same stale
        value. Only the first of them should cause an exchange — the rest are
        holding a token that has already been replaced, and clearing on their
        behalf would discard a good token and stampede intake.

        The lookup is by token value because a rejected caller has a token, not
        a URL.

        :param stale_token: The token the caller was rejected for; ignored when
            it is no longer the one held for its base URL.
        """
        if stale_token is None:
            return

        with self._lock:
            self._entries = {
                key: entry
                for key, entry in self._entries.items()
                if entry["token"] != stale_token
            }

    def clear(self) -> None:
        """Discards every held token, and every recorded failure with them."""
        with self._lock:
            self._entries = {}
            self._failures = {}

    def _record_failure(self, base_url: str, result: TokenResult) -> None:
        """Remembers why a mint produced no token, oldest out past the cap.
        Caller holds ``_lock``.

        Replaces the published map rather than mutating it, exactly as the token
        cache does: :meth:`last_failure` reads it on the fast path without the
        lock, and mutating in place would raise "dictionary changed size during
        iteration" in the matcher the moment a second target failed while
        another thread was querying. The trimming below is safe for the same
        reason it is trimming -- ``failures`` is a fresh local dict that nobody
        else can see until the assignment publishes it, which is one atomic
        rebind of the attribute.
        """
        failures = {**self._failures, base_url: result}
        while len(failures) > self._FAILURE_CAP:
            # Insertion order, so this is the oldest first-seen URL. A URL that
            # fails again keeps its original position, which is fine: the bound
            # is here to stop the map growing, not to model recency exactly.
            del failures[next(iter(failures))]
        self._failures = failures

    def _forget_failures(self, base_url: str, key: str) -> None:
        """Drops the records a successful mint has just answered. Caller holds
        ``_lock``.

        Both the URL that was asked for and everything under the canonical key
        intake resolved to: the failure may well have been recorded under a
        deeper resource URL than the one the token ends up keyed on.
        """
        if not self._failures:
            return
        self._failures = {
            url: failure
            for url, failure in self._failures.items()
            if url != base_url and url != key and not url.startswith(key + "/")
        }

    @staticmethod
    def _match_key(base_url: str, entries: dict) -> Optional[str]:
        """Returns the longest key in *entries* covering *base_url*, or ``None``.

        Deliberately not a port of intake's matcher: no normalization on either
        side. A caller that passes a non-canonical URL simply misses and mints
        again, which costs one HTTP call and is never a wrong answer.

        Takes *entries* rather than reading ``self._entries`` so the caller
        decides which snapshot is being scanned.

        A nil/empty *base_url* is always "no match", checked before the loop
        rather than left to be discovered by it. Otherwise a cold cache (the
        loop body never runs) and a warm one (``None.startswith(...)`` raises
        ``AttributeError``) would disagree on the outcome of the exact same
        call, decided only by unrelated earlier traffic.
        """
        if not base_url:
            return None

        best = None
        for key in entries:
            if base_url == key or base_url.startswith(key + "/"):
                if best is None or len(key) > len(best):
                    best = key
        return best

    def _match(self, base_url: str) -> Optional[dict]:
        entries = self._entries  # One atomic read; writes replace, never mutate.
        key = self._match_key(base_url, entries)
        return entries.get(key) if key is not None else None

    @staticmethod
    def _failure_reason(result: TokenResult) -> str:
        """Why a mint produced no usable token, for the log.

        The rejected-credential case never reaches here: it has a line of its
        own, because it is the only one an operator has to act on.
        """
        if result.outcome is TokenOutcome.TRANSPORT_ERROR:
            return "no response"

        payload = result.payload
        detail = payload.get("error") if payload else None
        status = result.status

        if status is not None and 200 <= status < 300:
            # A 2xx that produced no usable mint. The status says nothing here
            # -- intake claimed it worked -- so the shape of what came back is
            # the whole value of the line.
            if payload is None:
                # The parse error itself was logged by GenerateAccessToken.
                return f"HTTP {status} with an unreadable body"
            if detail:
                return str(detail)
            if payload.get("token"):
                # Distinct from a rejected request: intake's base_url is NOT
                # NULL, and it answers 422 rather than minting when the caller's
                # URL resolves to no environment.
                return "response carried a token but no base_url"
            return "no token in response"

        # The status is the actionable part, so it leads even when intake also
        # sent a message.
        return f"HTTP {status}{': ' + str(detail) if detail else ''}"

    @staticmethod
    def _usable(entry: Optional[dict]) -> bool:
        return bool(entry and entry["expired_at"] > datetime.now(tz=timezone.utc) + _REFRESH_BUFFER)

    @staticmethod
    def _parse_expiry(value) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(tz=timezone.utc) + timedelta(hours=1)
