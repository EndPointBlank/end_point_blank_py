"""
The error raised when no access token can be obtained for an outbound call.

Imports only modules that import nothing from the package
(:mod:`~end_point_blank.tokens.token_result`,
:mod:`~end_point_blank.strip_url`), so both
:class:`~end_point_blank.authorization.Authorization` and callers can import it
without joining the ``Authorization`` -> ``AccessTokens`` ->
``GenerateAccessToken`` cycle.
"""
from __future__ import annotations

from typing import Optional

from .strip_url import strip_url
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

    :ivar base_url: The URL a token was requested for, stripped to scheme,
        host, port and path (see :func:`~end_point_blank.strip_url.strip_url`),
        or ``None`` when it could not be parsed. Userinfo, query and fragment
        can carry a secret, and error reporters capture an exception's
        attributes as well as its message, so they are kept nowhere on the
        error; the caller already has the URL it passed.
    :ivar failure: The outcome and HTTP status of the failed mint made for
        this call, as a
        :class:`~end_point_blank.tokens.token_result.TokenResult` without its
        payload: a 2xx that could not be keyed carries a live token, and
        intake's body text is not this error's to repeat.
        ``Authorization.header`` always supplies it; it is ``None`` only when
        the error is constructed without one.
    :ivar status: ``failure.status`` -- the HTTP status intake answered the
        mint with -- or ``None`` when no response arrived or there is no
        failure. Distinguishes, say, a 400 from a 422 inside
        :attr:`~TokenOutcome.REQUEST_REJECTED`.

    :ivar unexpected: ``True`` when the mint raised rather than reporting a
        failure -- a bug, not an unreachable intake. Such a mint is reported as
        :attr:`~TokenOutcome.TRANSPORT_ERROR` with the exception as ``cause``
        (and ``__cause__``); its text is deliberately not copied into the
        message. The Ruby gem's ``unexpected?``.
    """

    def __init__(
        self,
        base_url: Optional[str],
        failure: Optional[TokenResult] = None,
        cause: Optional[BaseException] = None,
        unexpected: bool = False,
    ) -> None:
        stripped = strip_url(base_url)
        super().__init__(self._message(stripped, failure, unexpected))
        self.base_url = stripped
        self.failure = (
            TokenResult(failure.outcome, failure.status) if failure is not None else None
        )
        self.status: Optional[int] = failure.status if failure is not None else None
        self.cause = cause
        self.unexpected = unexpected
        if cause is not None:
            self.__cause__ = cause

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
        return (self.__class__, (self.base_url, self.failure, self.cause, self.unexpected))

    @staticmethod
    def _message(
        stripped: Optional[str], failure: Optional[TokenResult], unexpected: bool
    ) -> str:
        return (
            f"Could not mint an EndPointBlank access token for {_describe_url(stripped)}: "
            f"{_reason(failure, unexpected)}. {CREDENTIALS_NEVER_SENT}"
        )


def _describe_url(stripped: Optional[str]) -> str:
    """The stripped URL, or a fixed phrase when there is none to show."""
    if stripped is None:
        return "the requested URL (not shown: it could not be parsed)"
    return stripped


def _reason(failure: Optional[TokenResult], unexpected: bool) -> str:
    """The same fixed texts in every SDK (sc-1469). Never intake's response body
    or an exception's message or class name: neither is ours to vouch for, and
    either may carry anything."""
    # Before the outcome, because a mint that raised is also TRANSPORT_ERROR.
    if unexpected:
        return "the token request failed unexpectedly"
    if failure is None:
        # Unreachable from Authorization.header, which always passes the result
        # of its own mint; kept so a hand-built error still renders.
        return "the token request failed for an unknown reason"
    http = f" (HTTP {failure.status})" if failure.status is not None else ""
    if failure.outcome is TokenOutcome.CREDENTIAL_REJECTED:
        return (
            f"intake rejected this application's client credential{http}; "
            "retrying cannot help -- re-issue the credential"
        )
    if failure.outcome is TokenOutcome.REQUEST_REJECTED:
        return (
            f"intake refused the token request{http}; check the URL and that a "
            "grant covers the target"
        )
    if failure.outcome is TokenOutcome.SERVER_ERROR:
        return f"intake failed to issue a token{http}; this may be transient"
    if failure.outcome is TokenOutcome.TRANSPORT_ERROR:
        return (
            "intake could not be reached (timeout, connection refused or retries "
            "exhausted); this may be transient"
        )
    return "the token request failed for an unknown reason"
