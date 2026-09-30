"""
The error raised when no access token can be obtained for an outbound call.

Imports nothing from the package at module level, like
:mod:`~end_point_blank.tokens.token_result`, so both
:class:`~end_point_blank.authorization.Authorization` and callers can import it
without joining the ``Authorization`` -> ``AccessTokens`` ->
``GenerateAccessToken`` cycle.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from .tokens.token_result import TokenOutcome, TokenResult

# The one sentence every instance carries. It is the rule, not a detail: an
# integrator who hits this during an intake outage is looking for the fallback,
# and has to be told there is none by design rather than left to add one.
CREDENTIALS_NEVER_SENT = (
    "EndPointBlank never sends this service's client_id/client_secret to a "
    "provider, so there is no Basic-auth fallback and the call must not be made "
    "without a token."
)


class TokenUnavailableError(Exception):
    """
    Raised by :meth:`Authorization.header
    <end_point_blank.authorization.Authorization.header>` when no access token
    could be minted for the URL about to be called (sc-1469).

    Until sc-1469 the SDK fell back to HTTP Basic ``client_id:client_secret``
    here, which handed this service's own credential to whichever provider it
    was calling -- during every intake outage, timeout or revoked credential.
    It now refuses instead. Do not catch this and send Basic yourself: the
    provider is not EndPointBlank and must never see the credential.

    :ivar base_url: The URL a token was requested for.
    :ivar failure: The
        :class:`~end_point_blank.tokens.token_result.TokenResult` of the failed
        mint made for this call. ``Authorization.header`` always supplies it;
        it is ``None`` only when the error is constructed without one.
    :ivar status: ``failure.status`` -- the HTTP status intake answered the
        mint with -- or ``None`` when no response arrived or there is no
        failure. Distinguishes, say, a 400 from a 422 inside
        :attr:`~TokenOutcome.REQUEST_REJECTED`.
    """

    def __init__(self, base_url: str, failure: Optional[TokenResult] = None) -> None:
        super().__init__(self._message(base_url, failure))
        self.base_url = base_url
        self.failure = failure
        self.status: Optional[int] = failure.status if failure is not None else None

    @property
    def outcome(self) -> Optional[TokenOutcome]:
        """The failed mint's :class:`TokenOutcome`, or ``None`` if unknown.

        Branch on this to decide whether to retry: a
        :attr:`~TokenOutcome.TRANSPORT_ERROR` or :attr:`~TokenOutcome.SERVER_ERROR`
        may clear on its own; a :attr:`~TokenOutcome.CREDENTIAL_REJECTED` or
        :attr:`~TokenOutcome.REQUEST_REJECTED` will not.
        """
        return self.failure.outcome if self.failure is not None else None

    def __reduce__(self):
        # Exception's default rebuilds from self.args (the message) alone, which
        # would pass the message in as base_url and lose the failure.
        return (self.__class__, (self.base_url, self.failure))

    @staticmethod
    def _message(base_url: str, failure: Optional[TokenResult]) -> str:
        return (
            f"Could not mint an EndPointBlank access token for {_describe_url(base_url)}: "
            f"{_reason(failure)}. {CREDENTIALS_NEVER_SENT}"
        )


def _describe_url(base_url) -> str:
    """The URL as the message may show it: scheme, host and path only.

    The caller controls ``base_url``, and its userinfo, query or fragment can
    carry a secret; the message is what reaches logs and error reporting, so
    they are dropped here. The raw value stays on ``base_url``.
    """
    try:
        parts = urlsplit(str(base_url))
        host = parts.netloc.rpartition("@")[2]
    except ValueError:
        parts, host = None, ""
    if parts is None or not parts.scheme or not host:
        return "the requested URL (not shown: it could not be parsed)"
    return f"{parts.scheme}://{host}{parts.path}"


def _reason(failure: Optional[TokenResult]) -> str:
    if failure is None:
        # Unreachable from Authorization.header, which always passes the result
        # of its own mint; kept so a hand-built error still renders.
        return "the reason is unknown"
    if failure.outcome is TokenOutcome.CREDENTIAL_REJECTED:
        return (
            "EndPointBlank rejected this service's credential (HTTP 401); the "
            "client_id/client_secret is invalid or revoked and must be re-issued"
        )
    if failure.outcome is TokenOutcome.TRANSPORT_ERROR:
        return "EndPointBlank could not be reached (network error or timeout)"
    # Deferred so importing this error never loads the token cache.
    from .tokens.access_tokens import AccessTokens
    return AccessTokens._failure_reason(failure)
