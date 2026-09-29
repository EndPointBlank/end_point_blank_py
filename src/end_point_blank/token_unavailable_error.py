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
    :ivar failure: The recorded
        :class:`~end_point_blank.tokens.token_result.TokenResult` explaining the
        failed mint, or ``None`` if none was recorded (for example, another
        thread's successful mint cleared it in between).
    """

    def __init__(self, base_url: str, failure: Optional[TokenResult] = None) -> None:
        super().__init__(self._message(base_url, failure))
        self.base_url = base_url
        self.failure = failure

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
            f"Could not mint an EndPointBlank access token for {base_url}: "
            f"{_reason(failure)}. {CREDENTIALS_NEVER_SENT}"
        )


def _reason(failure: Optional[TokenResult]) -> str:
    if failure is None:
        return "no reason was recorded"
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
