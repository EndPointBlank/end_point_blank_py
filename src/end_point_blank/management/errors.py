"""
Errors raised by :class:`~end_point_blank.management.ManagementClient`.

The management API answers every error as one shape::

    {"error": {"code": "not_found", "message": "Not found.", "details": {...}}}

``code`` is stable and meant for programs; ``message`` is for people and may
change; ``details`` is present only when there is something field-level to say.
Every error is raised as a :class:`ManagementApiError` (or a subclass chosen by
its code), carrying ``code``, ``message``, ``details`` and the HTTP ``status``,
so a caller can match on ``error.code`` or catch a subclass.

A code this SDK does not know yet is never a crash: it is raised as the
subclass for its HTTP status (or :class:`ManagementApiError` itself) with the
code exactly as the API sent it.

This module imports nothing from the package.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Type


class ErrorCode(str, Enum):
    """
    Every ``error.code`` the management API documents, as a ``str`` enum, so
    ``error.code == ErrorCode.NOT_FOUND`` and ``error.code == "not_found"`` are
    the same test. ``error.code`` itself is always a plain ``str``: an
    unknown code is kept as sent rather than refused.

    ``CONNECTION_ERROR``, ``REQUEST_ERROR`` and ``INVALID_RESPONSE`` are this
    SDK's own, for a request that never got an answer, one that could not be
    sent, and an answer it could not read; the API never sends them.
    """

    # Authentication and limits
    MISSING_KEY = "missing_key"
    INVALID_KEY = "invalid_key"
    RUNTIME_CREDENTIAL_REFUSED = "runtime_credential_refused"
    INSUFFICIENT_SCOPE = "insufficient_scope"
    AUDIT_UNAVAILABLE = "audit_unavailable"
    RATE_LIMITED = "rate_limited"
    PLAN_LIMIT = "plan_limit"
    # Requests
    VALIDATION_FAILED = "validation_failed"
    NOT_FOUND = "not_found"
    INVALID_PAGINATION = "invalid_pagination"
    INVALID_FILTER = "invalid_filter"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    IDEMPOTENCY_REQUEST_IN_PROGRESS = "idempotency_request_in_progress"
    IDEMPOTENCY_REPLAY_UNAVAILABLE = "idempotency_replay_unavailable"
    BAD_REQUEST = "bad_request"
    INTERNAL_SERVER_ERROR = "internal_server_error"
    # Applications, environments and API packages
    HAS_DEPENDENTS = "has_dependents"
    PROTECTED = "protected"
    INVALID_ENVIRONMENT_BASE_URLS = "invalid_environment_base_urls"
    API_PACKAGE_ASSIGNED = "api_package_assigned"
    INTAKE_SYNC_FAILED = "intake_sync_failed"
    # Credentials
    DELETE_REFUSED = "delete_refused"
    INTAKE_CREDENTIAL = "intake_credential"
    INTAKE_REJECTED = "intake_rejected"
    INTAKE_UNAVAILABLE = "intake_unavailable"
    # Clients, packages and grants
    INVALID_CONTACTS = "invalid_contacts"
    INVALID_PACKAGES = "invalid_packages"
    INVALID_GRANTS = "invalid_grants"
    INVALID_MANAGED = "invalid_managed"
    CLIENT_NOT_ACCEPTED = "client_not_accepted"
    CLIENT_ACCEPTED = "client_accepted"
    CLIENT_NOT_MANAGED = "client_not_managed"
    ALREADY_A_MEMBER = "already_a_member"
    RETURN_TO_NOT_REGISTERED = "return_to_not_registered"
    MANAGED_CLIENT_HAS_CREDENTIALS = "managed_client_has_credentials"
    API_PACKAGE_NOT_FOUND = "api_package_not_found"
    ENVIRONMENT_NOT_FOUND = "environment_not_found"
    ALREADY_ASSIGNED = "already_assigned"
    NOTHING_PUBLISHED_IN_ENVIRONMENT = "nothing_published_in_environment"
    APPLICATION_NOT_FOUND = "application_not_found"
    ENDPOINT_NOT_FOUND = "endpoint_not_found"
    ENVIRONMENT_NOT_IN_APPLICATION = "environment_not_in_application"
    ALREADY_GRANTED = "already_granted"
    GRANT_REVOKED_CONCURRENTLY = "grant_revoked_concurrently"
    # Warnings (in a 2xx body's ``warnings``, never an error)
    ASSIGNMENT_DERIVES_NOTHING = "assignment_derives_nothing"
    # This SDK's own
    CONNECTION_ERROR = "connection_error"
    REQUEST_ERROR = "request_error"
    INVALID_RESPONSE = "invalid_response"


class ManagementApiError(Exception):
    """
    A management API request that did not succeed.

    :ivar code: The API's ``error.code`` (a ``str``; compare it with
        :class:`ErrorCode` or a literal). For an error body that is not the
        API's JSON shape -- say an HTML page from a proxy -- it is
        ``"http_<status>"``.
    :ivar message: The API's human-readable ``error.message``.
    :ivar details: The API's ``error.details`` (field errors for
        ``validation_failed``, ``published_in`` for
        ``nothing_published_in_environment``), or ``None``.
    :ivar status: The HTTP status, or ``None`` when no response arrived.
    :ivar retry_after: Seconds from the ``Retry-After`` header, or ``None``.
    :ivar method: The HTTP method of the request.
    :ivar path: The request path under the base URL (no query string).

    The management key is never part of an error: not in its message, its
    attributes or its ``repr``.
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        status: Optional[int] = None,
        retry_after: Optional[float] = None,
        method: Optional[str] = None,
        path: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status = status
        self.retry_after = retry_after
        self.method = method
        self.path = path
        self.location = location

    def __str__(self) -> str:
        where = f" ({self.method} {self.path})" if self.method and self.path else ""
        status = f"{self.status} " if self.status is not None else ""
        return f"{status}{self.code}: {self.message}{where}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, status={self.status!r}, "
            f"message={self.message!r})"
        )


class ManagementConnectionError(ManagementApiError):
    """No response arrived (connection refused or dropped, DNS, TLS, timeout).
    ``code`` is ``connection_error`` and ``status`` is ``None``."""


class InvalidResponseError(ManagementApiError):
    """A 2xx answer this SDK could not read as JSON. ``code`` is
    ``invalid_response``."""


class BadRequestError(ManagementApiError):
    """400: ``invalid_pagination``, ``invalid_idempotency_key``,
    ``bad_request`` (a request that could not be read) and any other 400."""


class AuthenticationError(ManagementApiError):
    """401: ``missing_key``, ``invalid_key``, ``runtime_credential_refused``."""


class PlanLimitError(ManagementApiError):
    """402 ``plan_limit``: the plan's limit for this resource is reached."""


class InsufficientScopeError(ManagementApiError):
    """403 ``insufficient_scope``: the key is read-only and the request writes."""


class NotFoundError(ManagementApiError):
    """404 ``not_found``: no such resource in your organization, or no such
    ``/api/v1`` path. Another organization's id answers this too."""


class ConflictError(ManagementApiError):
    """409: ``idempotency_request_in_progress``, ``intake_credential``,
    ``grant_revoked_concurrently`` and any other 409."""


class IdempotencyReplayUnavailableError(ConflictError):
    """
    409 ``idempotency_replay_unavailable``: an earlier request with this
    ``Idempotency-Key`` already succeeded, and its answer held a secret shown
    only once (a credential's create or rotate), so it cannot be replayed.

    The first request did take effect. Do not retry it: read or list the
    resource (``location``, when the API sent it, names it) to see its current
    state, and rotate the credential if its secret was lost.
    """


class ValidationFailedError(ManagementApiError):
    """422 ``validation_failed``: ``details`` lists the invalid fields."""


class RequestRefusedError(ManagementApiError):
    """422 with any code but ``validation_failed``: the request was understood
    and refused (``has_dependents``, ``already_assigned``,
    ``nothing_published_in_environment``, ``intake_rejected`` ...)."""


class RateLimitedError(ManagementApiError):
    """429 ``rate_limited``. ``retry_after`` says how many seconds to wait."""


class ServerError(ManagementApiError):
    """5xx: ``internal_server_error`` and any other 5xx not listed below."""


class ServiceUnavailableError(ServerError):
    """503: ``audit_unavailable`` or ``intake_unavailable``. Nothing was
    changed; the request is safe to retry."""


_BY_CODE: Dict[str, Type[ManagementApiError]] = {
    ErrorCode.MISSING_KEY.value: AuthenticationError,
    ErrorCode.INVALID_KEY.value: AuthenticationError,
    ErrorCode.RUNTIME_CREDENTIAL_REFUSED.value: AuthenticationError,
    ErrorCode.INSUFFICIENT_SCOPE.value: InsufficientScopeError,
    ErrorCode.PLAN_LIMIT.value: PlanLimitError,
    ErrorCode.NOT_FOUND.value: NotFoundError,
    ErrorCode.VALIDATION_FAILED.value: ValidationFailedError,
    ErrorCode.RATE_LIMITED.value: RateLimitedError,
    ErrorCode.IDEMPOTENCY_REPLAY_UNAVAILABLE.value: IdempotencyReplayUnavailableError,
    ErrorCode.AUDIT_UNAVAILABLE.value: ServiceUnavailableError,
    ErrorCode.INTAKE_UNAVAILABLE.value: ServiceUnavailableError,
    ErrorCode.INTERNAL_SERVER_ERROR.value: ServerError,
    ErrorCode.CONNECTION_ERROR.value: ManagementConnectionError,
    ErrorCode.INVALID_RESPONSE.value: InvalidResponseError,
}

_BY_STATUS: Dict[int, Type[ManagementApiError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: PlanLimitError,
    403: InsufficientScopeError,
    404: NotFoundError,
    409: ConflictError,
    422: RequestRefusedError,
    429: RateLimitedError,
    503: ServiceUnavailableError,
}


def error_class(code: Optional[str], status: Optional[int]) -> Type[ManagementApiError]:
    """The class an error with this code and status is raised as: by code
    first, then by status, then :class:`ManagementApiError`."""
    if code in _BY_CODE:
        return _BY_CODE[code]
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    if status is not None and status >= 500:
        return ServerError
    return ManagementApiError
