from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..authorization import Authorization
from ..configuration import Configuration
from ..tokens.token_result import TokenOutcome, TokenResult
from ._http import post

logger = logging.getLogger(__name__)


class GenerateAccessToken:
    """
    Generates an access token by calling the EndPointBlank API.

    Sends the base URL (and optional TTL) to the configured ``access_token_url``.

    :meth:`token_result` is the entry point to reach for: it reports *why* a
    mint ended the way it did, which is the difference between a credential that
    has to be re-issued and an outage that will clear on its own.
    :meth:`token` is the older, status-blind form, kept because it is published
    API.

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::GenerateAccessToken``.
    """

    @staticmethod
    def token_result(base_url: str) -> TokenResult:
        """
        Requests a new access token for *base_url* and reports how it went.

        :param base_url: Sent verbatim. intake normalizes it and matches it
            against registered base URLs by longest path prefix.
        :returns: A :class:`~end_point_blank.tokens.token_result.TokenResult`.
            Never ``None`` -- every failure mode is one of the outcomes.
        """
        config = Configuration()
        body: Dict[str, Any] = {"base_url": base_url}
        if config.token_ttl is not None:
            body["token_ttl"] = config.token_ttl

        response = post(config.access_token_url, Authorization.header(), body)

        # TRANSPORT_ERROR means one thing and only one thing: no usable HTTP
        # status was obtained. ``post`` has already spent its three attempts by
        # the time it answers None, so this is a failed network, not an untried
        # one -- and it is the ONLY way to reach that outcome. Anything with a
        # status is classified by the status; see below.
        if response is None:
            logger.error("Access token request to %s did not complete", base_url)
            return TokenResult(TokenOutcome.TRANSPORT_ERROR)

        status = response.status_code
        logger.info("Access token response: %s", status)

        # Classify on the status FIRST, parse the body second. Deciding on the
        # parse first would make a 401 whose body is not JSON read as transient,
        # and the caller would retry a dead credential forever -- the exact bug
        # this exists to remove. "intake always sends JSON" is no protection:
        # the SDK reaches intake through Caddy, and any proxy, WAF or auth
        # gateway in front of the app can answer 401 with an HTML error page the
        # app never generated. The credential really is rejected and the body
        # really is unreadable.
        outcome = GenerateAccessToken._outcome(status)

        # Broad on purpose: requests and httpx raise different errors for a body
        # that is not JSON, and a mangled body raises from deeper still.
        try:
            payload: Optional[Dict[str, Any]] = response.json()
        except Exception as exc:
            logger.error("Failed to parse access token response: %s", exc)
            payload = None
            if outcome is TokenOutcome.SUCCESS:
                # The one case where the body decides. A 2xx is worth nothing
                # but the token in it, so a success the SDK cannot read is a
                # broken server rather than a success.
                outcome = TokenOutcome.SERVER_ERROR

        if outcome is TokenOutcome.SUCCESS and not GenerateAccessToken._is_usable(payload):
            # A 2xx that minted nothing usable. It keeps its real 2xx status --
            # that is what happened -- but it is not a SUCCESS: SUCCESS means a
            # token and somewhere to key it, and a caller acting on the name
            # alone would cache nothing and never know why.
            #
            # SERVER_ERROR rather than a rejection, because intake's base_url is
            # NOT NULL and it answers 422 rather than minting when the URL
            # resolves to no environment. A 2xx without one is a server that
            # broke its own contract, not a request that was refused.
            outcome = TokenOutcome.SERVER_ERROR

        return TokenResult(outcome, status, payload)

    @staticmethod
    def token(base_url: str) -> Optional[Dict[str, Any]]:
        """
        Requests a new access token for *base_url*.

        Kept for callers written against it, and unchanged: it answers the
        parsed body for *any* status intake returned -- so a rejected credential
        and a broken server are the same value here. Use :meth:`token_result` to
        tell them apart.

        :param base_url: Sent verbatim. intake normalizes it and matches it
            against registered base URLs by longest path prefix.
        :returns: A dict with ``token``, ``expired_at`` and ``base_url``, or
            ``None`` on failure.
        """
        return GenerateAccessToken.token_result(base_url).payload

    @staticmethod
    def _is_usable(payload: Optional[Dict[str, Any]]) -> bool:
        """Whether a 2xx body is actually a mint.

        ``base_url`` is the canonical URL intake resolved the request to, and it
        is the only thing the token can be cached under -- keying on the URL the
        caller asked about would store an entry per resource, with nothing to
        evict them.
        """
        return bool(payload and payload.get("token") and payload.get("base_url"))

    @staticmethod
    def _outcome(status: int) -> TokenOutcome:
        """Maps intake's status onto what a caller can do about it.

        401 is singled out because it is the one failure whose remedy is the
        credential itself; intake answers it (rather than 422) for exactly that
        reason. Every other 4xx -- a malformed request, or an environment that
        is not registered -- is just as permanent, so it must not land in
        ``SERVER_ERROR``, whose name invites a retry.
        """
        if 200 <= status < 300:
            return TokenOutcome.SUCCESS
        if status == 401:
            return TokenOutcome.CREDENTIAL_REJECTED
        if 400 <= status < 500:
            return TokenOutcome.REQUEST_REJECTED
        return TokenOutcome.SERVER_ERROR
