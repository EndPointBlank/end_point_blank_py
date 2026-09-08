"""
The outcome of one attempt to mint an access token.

Lives in its own module because both ends of the mint need it and they cannot
import each other: ``Authorization`` reaches for ``AccessTokens``, which reaches
for ``GenerateAccessToken``, which reaches back for ``Authorization``. Both of
those hops are deliberately deferred to call time to break the cycle. This
module imports nothing from the package, so both ends can import it normally.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class TokenOutcome(Enum):
    """
    Why a mint ended the way it did, at the granularity a caller can act on.

    intake's access-token endpoint answers 201 on success, 400 for a malformed
    request (a bad ``token_ttl``, a missing ``base_url``), 401 for an invalid or
    revoked credential, 422 when the URL resolves to no registered application
    environment, and 5xx for a genuine fault. Those do not reduce to
    "worked" and "did not work": the remedy differs, and so does whether
    retrying is worth anything at all.

    - :attr:`SUCCESS` -- 2xx. The payload is the mint.
    - :attr:`CREDENTIAL_REJECTED` -- 401. Permanent until the credential is
      re-issued. No amount of retrying changes the answer.
    - :attr:`REQUEST_REJECTED` -- any other 4xx. Also permanent, but the
      credential is fine: the environment is not registered, or the request was
      malformed. Deliberately not folded in with :attr:`SERVER_ERROR`, whose
      name would tell a caller to retry into a wall.
    - :attr:`SERVER_ERROR` -- 5xx. Transient; retrying is reasonable.
    - :attr:`TRANSPORT_ERROR` -- no usable response at all: the network failed,
      the request timed out, ``post`` exhausted its retries, or a 2xx body could
      not be parsed. Transient.
    """

    SUCCESS = "success"
    CREDENTIAL_REJECTED = "credential_rejected"
    REQUEST_REJECTED = "request_rejected"
    SERVER_ERROR = "server_error"
    TRANSPORT_ERROR = "transport_error"


_RETRYABLE = frozenset({TokenOutcome.SERVER_ERROR, TokenOutcome.TRANSPORT_ERROR})


@dataclass(frozen=True)
class TokenResult:
    """
    One mint attempt: the verdict, the HTTP status behind it, and the parsed
    body if there was one.

    Frozen because instances are handed out by
    :meth:`~end_point_blank.tokens.access_tokens.AccessTokens.last_failure` and
    read from threads other than the one that recorded them.

    :param outcome: The verdict. Branch on this.
    :param status: The HTTP status, or ``None`` when no response arrived. Read
        it when the outcome is coarser than the decision being made -- a 400 and
        a 422 are both :attr:`TokenOutcome.REQUEST_REJECTED`, but only one of
        them means "register this environment".
    :param payload: The parsed response body, or ``None`` when there was none or
        it could not be parsed. On a :attr:`TokenOutcome.SUCCESS` this carries
        ``token``, ``expired_at`` and ``base_url``.
    """

    outcome: TokenOutcome
    status: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None

    @property
    def succeeded(self) -> bool:
        """Whether intake answered 2xx. Not whether a usable token came back --
        a 2xx missing ``token`` or ``base_url`` still succeeded by this measure
        and still mints nothing."""
        return self.outcome is TokenOutcome.SUCCESS

    @property
    def retryable(self) -> bool:
        """Whether trying again could plausibly produce a different answer.

        ``False`` for every 4xx, including 401: those need something outside
        this process to change first.
        """
        return self.outcome in _RETRYABLE
