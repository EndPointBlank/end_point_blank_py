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
        if response is None:
            # ``post`` has already spent its three attempts, so this is a
            # failed network, not an untried one.
            logger.error("Access token request to %s did not complete", base_url)
            return TokenResult(TokenOutcome.TRANSPORT_ERROR)

        status = response.status_code
        logger.info("Access token response: %s", status)

        try:
            payload: Optional[Dict[str, Any]] = response.json()
        except Exception as exc:
            logger.error("Failed to parse access token response: %s", exc)
            payload = None
            if 200 <= status < 300:
                # A 2xx is worth nothing but the token in it. With no readable
                # body there is no token, and nothing separates this from the
                # response never arriving.
                return TokenResult(TokenOutcome.TRANSPORT_ERROR, status)
            # On a non-2xx the status is the actionable part and the body is
            # decoration, so an unreadable body does not change the verdict. A
            # proxy answering 401 with an HTML page is still a rejected
            # credential; calling it a transport error would restart the retry
            # loop that a 401 exists to stop.

        return TokenResult(GenerateAccessToken._outcome(status), status, payload)

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
