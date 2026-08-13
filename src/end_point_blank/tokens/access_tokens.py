from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

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
    Equivalent to the Ruby gem's ``EndPointBlank::AccessTokens``.
    """

    _instance: Optional["AccessTokens"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "AccessTokens":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._entries: dict = {}
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
            payload = GenerateAccessToken.token(base_url)

            # The key is what intake resolved to, and only that. There is no
            # fallback to the requested URL: that would key on the resource the
            # caller happened to ask about, so a service walking /orders/1,
            # /orders/2, /orders/3 would mint and store a token per resource,
            # and nothing here evicts. Without a base URL the right application
            # cannot be found, so no token is handed back either.
            key = payload.get("base_url") if payload else None

            if payload and payload.get("token") and key:
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
                return payload["token"]

            # A failed refresh must not leave an expiring token behind claiming
            # to be usable — callers would keep presenting it right up to the
            # 401. Only the entry that covers this URL goes: the longest match
            # is the one that was just found unusable, so a shorter, still-good
            # entry survives.
            stale = matched_key
            if stale is not None:
                self._entries = {k: v for k, v in self._entries.items() if k != stale}
            logger.error(
                "Failed to generate access token for %s: %s",
                base_url,
                self._failure_reason(payload),
            )
            return None

    def exists(self, base_url: str) -> bool:
        """Returns ``True`` if a token covering *base_url* has 30+ seconds left."""
        entry = self._match(base_url)
        return bool(entry and entry["expired_at"] > datetime.now(tz=timezone.utc) + _MIN_TTL)

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
        """Discards every held token."""
        with self._lock:
            self._entries = {}

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
    def _failure_reason(payload: Optional[dict]) -> str:
        """Why a mint produced no usable token, for the log."""
        if not payload:
            return "no response"
        if payload.get("error"):
            return payload["error"]
        if payload.get("token"):
            # Distinct from a rejected request: intake's base_url is NOT NULL,
            # and it answers 422 rather than minting when the caller's URL
            # resolves to no environment. A 201 without one is a broken server.
            return "response carried a token but no base_url"
        return "no token in response"

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
