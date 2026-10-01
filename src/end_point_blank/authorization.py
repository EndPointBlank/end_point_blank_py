from __future__ import annotations

import base64
import warnings

from .configuration import Configuration
from .configuration_error import ConfigurationError
from .strip_url import strip_url
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

        :param base_url: The URL you are about to call. Userinfo, query and
            fragment are removed before the token request (see
            :func:`~end_point_blank.strip_url.strip_url`): they are never sent
            to intake, logged, or kept on the error. A token covering the rest
            is used, and minted if necessary. Required: there is no
            no-argument form any more.
        :returns: ``"Bearer <token>"`` -- never anything else.
        :raises TokenUnavailableError: when no token could be minted (intake
            unreachable or timing out, a 401 for a revoked credential, a 4xx
            for an unregistered URL, a 5xx, or the mint raising). Its
            ``failure`` says which; the credential is not sent in its place.
            A mint that raised anything but :class:`ConfigurationError` is
            reported with ``unexpected`` true and the exception as ``cause``.
        :raises ValueError: when *base_url* is empty or ``None``, or is not an
            absolute http or https URL with a host. No request is made. (The Ruby
            gem raises ``ArgumentError`` here; ``ValueError`` is Python's.)
        :raises ConfigurationError: when ``client_id`` or ``client_secret`` is
            missing. No request is made.
        """
        if not base_url:
            raise ValueError(
                "Authorization.header requires the URL you are about to call; "
                "a provider call is only ever authorized with a Bearer token minted for it."
            )

        # Stripped before anything else sees it, so the token request, the
        # cache and failure keys, the log lines and the error all carry the
        # same safe form.
        url = strip_url(base_url)
        if url is None:
            # The URL itself is left out: it could not be parsed, so there is
            # no telling which part of it is a secret.
            raise ValueError(
                "Authorization.header requires an absolute http or https URL with a host, "
                "such as https://api.example.com/orders."
            )

        # Deferred to break the Authorization -> AccessTokens ->
        # GenerateAccessToken -> Authorization import cycle.
        from .tokens.access_tokens import AccessTokens
        from .tokens.token_result import TokenOutcome, TokenResult
        # token_result, not token() then last_failure(): the failure record is
        # shared per target and read outside the lock, so another thread could
        # overwrite or clear it in between and this call would report someone
        # else's reason. This result was captured under the lock for this call.
        try:
            result = AccessTokens().token_result(url)
        except ConfigurationError:
            # Nothing was sent and retrying cannot help: a missing credential
            # is the integrator's to fix, not an outage to wait out (sc-1469).
            raise
        except Exception as exc:
            # ``post`` answers None for a network failure, so anything raised
            # here is a bug, not an unreachable intake -- but it is still a
            # mint that produced no status, and header raises only the errors
            # it documents. It is marked unexpected so the reader is not sent
            # off to check the network; the exception rides along as the
            # cause, and its text stays out of the message.
            raise TokenUnavailableError(
                url, TokenResult(TokenOutcome.TRANSPORT_ERROR), cause=exc, unexpected=True
            ) from exc
        if result.outcome is TokenOutcome.SUCCESS:
            return f"Bearer {result.payload['token']}"
        raise TokenUnavailableError(url, result)

    @classmethod
    def _intake_header(cls) -> str:
        """
        The Basic header for calls to EndPointBlank's own intake only.

        Internal. Never use this for a call to a provider: that would send this
        service's own credential to a third party, which :meth:`header` exists
        to prevent.

        :raises ConfigurationError: when ``client_id`` or ``client_secret`` is
            unset or empty. Interpolating them would silently send
            ``None:None`` instead, which intake answers with a 401.
        """
        config = Configuration()
        client_id = config.client_id
        client_secret = config.client_secret
        missing = [
            name
            for name, value in (("client_id", client_id), ("client_secret", client_secret))
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"EndPointBlank is missing {' and '.join(missing)}: set it with "
                "end_point_blank.configure or ENDPOINTBLANK_CLIENT_ID / "
                "ENDPOINTBLANK_CLIENT_SECRET. The SDK cannot authenticate to its "
                "intake without both."
            )
        return f"Basic {_encode(client_id, client_secret)}"

    @staticmethod
    def basic_credentials() -> str:
        """Returns the Base64-encoded ``client_id:client_secret`` string.

        Emits a :class:`DeprecationWarning`.

        .. deprecated::
            The value is this service's own client secret and is only valid for
            this service's own EndPointBlank intake. Never send it to a
            provider; use ``Authorization.header(base_url)`` (sc-1469).
        """
        warnings.warn(
            "Authorization.basic_credentials is deprecated: it is this service's own "
            "client_id/client_secret and is only valid for this service's own "
            "EndPointBlank intake. Never send it to a provider; use "
            "Authorization.header(base_url) (sc-1469).",
            DeprecationWarning,
            stacklevel=2,
        )
        config = Configuration()
        return _encode(config.client_id, config.client_secret)


def _encode(client_id, client_secret) -> str:
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
