from __future__ import annotations

import base64

from .configuration import Configuration
from .token_unavailable_error import TokenUnavailableError


class Authorization:
    """
    Generates HTTP authorization headers for EndPointBlank API calls.

    :meth:`header` is for calls to a **provider** -- another application you
    are about to call. It only ever answers ``Bearer <token>``, and raises
    :class:`~end_point_blank.token_unavailable_error.TokenUnavailableError`
    when no token can be obtained. It never falls back to HTTP Basic: this
    service's ``client_id``/``client_secret`` must never reach a provider
    (sc-1469).

    Calls to EndPointBlank's own intake (authorize, token minting, endpoint
    registration, the log/request writers) present Basic credentials, which
    intake already holds. They use the internal :meth:`_intake_header`, which
    is not for application code.

    Equivalent to the Ruby gem's ``EndPointBlank::Authorization``.
    """

    @classmethod
    def header(cls, base_url: str) -> str:
        """
        Returns the ``Authorization`` header value for an outbound call to a
        provider.

        :param base_url: The URL you are about to call, with any query string
            and fragment removed. A token covering it is used, and minted if
            necessary. Required: there is no no-argument form any more.
        :returns: ``"Bearer <token>"`` -- never anything else.
        :raises TokenUnavailableError: when no token could be minted (intake
            unreachable or timing out, a 401 for a revoked credential, a 4xx
            for an unregistered URL, a 5xx). Its ``failure`` says which; the
            credential is not sent in its place.
        :raises ValueError: when *base_url* is empty or ``None``.
        """
        if not base_url:
            raise ValueError(
                "Authorization.header requires the URL you are about to call; "
                "a provider call is only ever authorized with a Bearer token minted for it."
            )

        # Deferred to break the Authorization -> AccessTokens ->
        # GenerateAccessToken -> Authorization import cycle.
        from .tokens.access_tokens import AccessTokens
        from .tokens.token_result import TokenOutcome
        # token_result, not token() then last_failure(): the failure record is
        # shared per target and read outside the lock, so another thread could
        # overwrite or clear it in between and this call would report someone
        # else's reason. This result was captured under the lock for this call.
        result = AccessTokens().token_result(base_url)
        if result.outcome is TokenOutcome.SUCCESS:
            return f"Bearer {result.payload['token']}"
        raise TokenUnavailableError(base_url, result)

    @classmethod
    def _intake_header(cls) -> str:
        """
        The Basic header for calls to EndPointBlank's own intake only.

        Internal. Never use this for a call to a provider: that would send this
        service's own credential to a third party, which :meth:`header` exists
        to prevent.
        """
        return f"Basic {cls.basic_credentials()}"

    @staticmethod
    def basic_credentials() -> str:
        """Returns the Base64-encoded ``client_id:client_secret`` string."""
        config = Configuration()
        raw = f"{config.client_id}:{config.client_secret}"
        return base64.b64encode(raw.encode()).decode()
