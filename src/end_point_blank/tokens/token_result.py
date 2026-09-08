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

    - :attr:`SUCCESS` -- 2xx carrying a usable mint: a non-empty ``token``, and
      the non-empty ``base_url`` to key it under. Nothing else is a success, so
      code that branches on this name can read both fields straight off the
      payload. An ``is_success`` that could be true with the token absent would
      leave every caller a second check to remember, which is the check that
      gets forgotten.
    - :attr:`CREDENTIAL_REJECTED` -- 401. Permanent until the credential is
      re-issued. No amount of retrying changes the answer.
    - :attr:`REQUEST_REJECTED` -- any other 4xx. Also permanent, but the
      credential is fine: the environment is not registered, or the request was
      malformed. Deliberately not folded in with :attr:`SERVER_ERROR`, whose
      name would tell a caller to retry into a wall.
    - :attr:`SERVER_ERROR` -- 5xx, and any 2xx that did not produce a usable
      mint: a body that would not parse, a body that is not a JSON object, no
      ``token``, or a token with no ``base_url``. intake's ``base_url`` is NOT
      NULL and it answers 4xx rather than minting when the URL resolves to
      nothing, so a 2xx missing one is a broken server, not a rejected request.
      The :attr:`status` stays the real 2xx, never a synthesized 5xx.
    - :attr:`TRANSPORT_ERROR` -- no usable HTTP status was obtained at all: the
      network failed, the request timed out, or ``post`` exhausted its retries.
      Transient.

    Outcomes are decided by the status first and the body second. A response
    that arrived is never :attr:`TRANSPORT_ERROR`, however unreadable it is --
    otherwise a 401 answered by a proxy with an HTML error page would read as
    transient and be retried forever.

    There is deliberately no "should I retry?" boolean. Two of these five are
    worth retrying and three are not, but which three is a caller's decision and
    the remedies differ; a predicate would collapse five honest names back into
    two, which is a smaller copy of the bug this exists to remove.
    """

    SUCCESS = "success"
    CREDENTIAL_REJECTED = "credential_rejected"
    REQUEST_REJECTED = "request_rejected"
    SERVER_ERROR = "server_error"
    TRANSPORT_ERROR = "transport_error"



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

        Kept verbatim on a failure too, including the 2xx bodies classified
        :attr:`TokenOutcome.SERVER_ERROR` for carrying no usable token. That is
        what the failure log reads to say *how* the body was useless, and what
        keeps ``GenerateAccessToken.token`` handing back exactly the body it
        always did.
    """

    outcome: TokenOutcome
    status: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
