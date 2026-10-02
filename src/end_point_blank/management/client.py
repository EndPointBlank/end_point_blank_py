"""
:class:`ManagementClient`: the EndPointBlank organization management API
(``/api/v1`` on the portal) for scripts and services that set up an
organization -- its API packages, clients, package assignments and grants,
applications, environments and runtime credentials -- without the portal.

It is separate from everything else in this package on purpose. It is
configured only by its constructor, never by :func:`end_point_blank.configure`
or an ``ENDPOINTBLANK_*`` variable, and it sends exactly one credential: the
management key, as ``Authorization: Bearer epb_mk_...``, to the portal. The
runtime ``client_id``/``client_secret`` are never sent to ``/api/v1`` (the API
refuses them with ``runtime_credential_refused``), and the management key is
never sent to intake.

It reuses the SDK's HTTP layer (:mod:`end_point_blank.commands._http`): the
same per-thread ``requests`` session and the same set of transport errors.
"""
from __future__ import annotations

import email.utils
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import quote, urlsplit

import requests

from .. import VERSION
from ..commands import _http
from ..configuration_error import ConfigurationError
from .errors import (
    ErrorCode,
    InvalidResponseError,
    ManagementApiError,
    ManagementConnectionError,
    error_class,
)
from .models import (
    Application,
    ApplicationEnvironment,
    ApiPackage,
    ClaimInvite,
    Client,
    Credential,
    Deleted,
    Endpoint,
    Environment,
    Organization,
    PackageAssignment,
    PackageEndpoint,
    PackageEndpointAdded,
    Grant,
    Page,
    ApiWarning,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://app.endpointblank.com"
KEY_PREFIX = "epb_mk_"
API_PREFIX = "/api/v1"
MAX_PAGE_SIZE = 100

_DEFAULT_TIMEOUT = (3.0, 30.0)  # connect, read (seconds)
_BACKOFF_BASE = 0.5  # seconds; doubled per attempt
_BACKOFF_CAP = 8.0

# Methods retried after a 5xx or a request that never got an answer. A POST is
# safe because every POST carries an Idempotency-Key, reused on the retry. A
# PATCH is not retried after either: it carries no key, and nothing says the
# first one did not apply.
_RETRY_AFTER_FAILURE = frozenset({"GET", "DELETE", "POST"})

_REPLAY_UNAVAILABLE_HINT = (
    " The first request with this Idempotency-Key succeeded, so it was not "
    "retried: read or list the resource to see its current state (rotate the "
    "credential if its secret was lost)."
)

Params = Dict[str, Any]
Body = Dict[str, Any]
Timeout = Union[float, Tuple[float, float]]


def _seg(value: Any, name: str) -> str:
    """An id for a path segment: a non-empty string (or a UUID), escaped so it
    cannot change the path."""
    if isinstance(value, uuid.UUID):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string id")
    return quote(value, safe="")


def _check_limit(limit: Optional[int]) -> None:
    if limit is None:
        return
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an int from 1 to {MAX_PAGE_SIZE}")


def _compact(values: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """``Retry-After`` as seconds: delta-seconds or an HTTP date."""
    if value is None:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        at = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return max(0.0, (at - datetime.now(timezone.utc)).total_seconds())


class _Transport:
    """Sends one management API request: headers, Idempotency-Key, retries and
    error mapping. Holds the key; nothing else in this module does."""

    def __init__(
        self,
        key: str,
        base_url: str,
        *,
        max_retries: int,
        max_retry_wait: float,
        timeout: Timeout,
        sleep: Callable[[float], None],
        session: Optional[requests.Session],
        user_agent: str,
    ) -> None:
        self._key = key
        self.base_url = base_url
        self.max_retries = max_retries
        self.max_retry_wait = max_retry_wait
        self.timeout = timeout
        self.sleep = sleep
        self._own_session = session
        self.user_agent = user_agent

    def __repr__(self) -> str:
        return f"<_Transport base_url={self.base_url!r} key={KEY_PREFIX}…redacted>"

    def _session(self) -> requests.Session:
        return self._own_session if self._own_session is not None else _http._session()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Params] = None,
        body: Optional[Body] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[Any, Mapping[str, str]]:
        """Returns ``(parsed JSON body, response headers)`` for a 2xx; raises
        :class:`ManagementApiError` otherwise."""
        url = self.base_url + API_PREFIX + path
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body)
        if method == "POST":
            # Generated once, before the first attempt, so every retry of this
            # call presents the same key and the API runs it at most once.
            key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
            if not isinstance(key, str) or not key.strip() or len(key) > 255:
                raise ValueError("idempotency_key must be 1 to 255 characters")
            headers["Idempotency-Key"] = key

        attempt = 0
        while True:
            try:
                response = self._session().request(
                    method,
                    url,
                    params=_compact(params or {}) or None,
                    data=data,
                    headers=headers,
                    timeout=self.timeout,
                )
            except _http.TRANSPORT_ERRORS as exc:
                # Named by class only: requests can quote a header value in an
                # exception's text, and the header here is the key.
                error = ManagementConnectionError(
                    f"The request did not complete ({type(exc).__name__}).",
                    code=ErrorCode.CONNECTION_ERROR.value,
                    method=method,
                    path=path,
                )
                delay = self._delay_after_failure(method, attempt) if method in _RETRY_AFTER_FAILURE else None
                if delay is None:
                    raise error from None
                logger.warning(
                    "EndPointBlank management API %s %s did not complete (%s); retry %d/%d in %.1fs",
                    method, path, type(exc).__name__, attempt + 1, self.max_retries, delay,
                )
                self.sleep(delay)
                attempt += 1
                continue

            if 200 <= response.status_code < 300:
                return self._parse_success(response, method, path), response.headers

            error = self._error(response, method, path)
            delay = self._retry_delay(method, error, attempt)
            if delay is None:
                raise error
            logger.warning(
                "EndPointBlank management API %s %s answered %s %s; retry %d/%d in %.1fs",
                method, path, error.status, error.code, attempt + 1, self.max_retries, delay,
            )
            self.sleep(delay)
            attempt += 1

    def _delay_after_failure(self, method: str, attempt: int) -> Optional[float]:
        if attempt >= self.max_retries:
            return None
        return min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** attempt))

    def _retry_delay(self, method: str, error: ManagementApiError, attempt: int) -> Optional[float]:
        if attempt >= self.max_retries:
            return None
        if error.status == 429:
            # Refused before anything ran, so even a PATCH is safe to resend.
            delay = error.retry_after
            if delay is None:
                delay = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** attempt))
            return delay if delay <= self.max_retry_wait else None
        if method not in _RETRY_AFTER_FAILURE:
            return None
        if error.code == ErrorCode.IDEMPOTENCY_REQUEST_IN_PROGRESS:
            return self._delay_after_failure(method, attempt)
        if error.status is not None and error.status >= 500:
            return self._delay_after_failure(method, attempt)
        return None

    @staticmethod
    def _parse_success(response: requests.Response, method: str, path: str) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise InvalidResponseError(
                f"The API answered {response.status_code} with a body that is not JSON.",
                code=ErrorCode.INVALID_RESPONSE.value,
                status=response.status_code,
                method=method,
                path=path,
            ) from None

    @staticmethod
    def _error(response: requests.Response, method: str, path: str) -> ManagementApiError:
        status = response.status_code
        retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
        location = response.headers.get("Location")
        code: Optional[str] = None
        message: Optional[str] = None
        details = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            code = error["code"]
            message = error.get("message") if isinstance(error.get("message"), str) else None
            details = error.get("details")
        if code is None:
            content_type = response.headers.get("Content-Type", "unknown content type")
            code = f"http_{status}"
            message = f"The API answered {status} without an error body ({content_type})."
        if message is None:
            message = f"The API answered {status} {code}."
        if code == ErrorCode.IDEMPOTENCY_REPLAY_UNAVAILABLE:
            message += _REPLAY_UNAVAILABLE_HINT
        cls = error_class(code, status)
        return cls(
            message,
            code=code,
            details=details,
            status=status,
            retry_after=retry_after,
            method=method,
            path=path,
            location=location,
        )


def _iterate(fetch: Callable[..., Page], limit: Optional[int], **kwargs: Any) -> Iterator[Any]:
    after = None
    while True:
        page = fetch(limit=limit, after=after, **kwargs)
        yield from page.items
        if not page.next_cursor:
            return
        after = page.next_cursor


class _OrganizationResources:
    """
    Applications, environments, application environments and credentials of
    one organization: the key's own on :class:`ManagementClient`, or a managed
    client's on :class:`ManagedClient`. The two differ only by path prefix.
    """

    _transport: _Transport
    _prefix: str

    # -- plumbing -----------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        payload, _headers = self._transport.request(method, self._prefix + path, **kwargs)
        return payload

    def _one(self, model: Any, method: str, path: str, **kwargs: Any) -> Any:
        return model.from_dict(self._data(self._call(method, path, **kwargs), method, path))

    def _page(self, model: Any, path: str, limit: Optional[int], after: Optional[str], **filters: Any) -> Page:
        _check_limit(limit)
        payload = self._call("GET", path, params={"limit": limit, "after": after, **filters})
        items = self._data(payload, "GET", path)
        if not isinstance(items, list):
            raise self._invalid("GET", path)
        return Page(items=[model.from_dict(i) for i in items], next_cursor=payload.get("next_cursor"))

    def _deleted(self, path: str) -> Deleted:
        payload = self._call("DELETE", path)
        return Deleted.from_dict(self._data(payload, "DELETE", path), payload.get("warnings"))

    def _data(self, payload: Any, method: str, path: str) -> Any:
        if not isinstance(payload, dict) or "data" not in payload:
            raise self._invalid(method, path)
        return payload["data"]

    @staticmethod
    def _invalid(method: str, path: str) -> InvalidResponseError:
        return InvalidResponseError(
            "The API answered without the expected data.",
            code=ErrorCode.INVALID_RESPONSE.value,
            method=method,
            path=path,
        )

    # -- applications ------------------------------------------------------

    def list_applications(self, *, limit: Optional[int] = None, after: Optional[str] = None) -> Page[Application]:
        """One page of applications (``GET /applications``)."""
        return self._page(Application, "/applications", limit, after)

    def iter_applications(self, *, limit: Optional[int] = None) -> Iterator[Application]:
        """Every application, fetching pages of ``limit`` as it goes."""
        return _iterate(self.list_applications, limit)

    def get_application(self, application_id: str) -> Application:
        return self._one(Application, "GET", f"/applications/{_seg(application_id, 'application_id')}")

    def create_application(
        self,
        name: str,
        environment_base_urls: Mapping[str, str],
        *,
        public: Optional[bool] = None,
        organization_group_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Application:
        """
        Creates an application, placed in each environment given a base URL
        (``environment_base_urls``: environment id to URL; at least one).
        """
        body = _compact(
            {
                "name": name,
                "environment_base_urls": dict(environment_base_urls),
                "public": public,
                "organization_group_id": organization_group_id,
            }
        )
        return self._one(Application, "POST", "/applications", body=body, idempotency_key=idempotency_key)

    def update_application(
        self, application_id: str, *, name: Optional[str] = None, public: Optional[bool] = None
    ) -> Application:
        """Renames an application or changes whether it is public. Not retried
        after a 5xx or a lost connection (a PATCH has no Idempotency-Key)."""
        body = _compact({"name": name, "public": public})
        return self._one(Application, "PATCH", f"/applications/{_seg(application_id, 'application_id')}", body=body)

    def delete_application(self, application_id: str) -> Deleted:
        """Refused with ``has_dependents`` while grants or credentials depend on it."""
        return self._deleted(f"/applications/{_seg(application_id, 'application_id')}")

    # -- application environments -----------------------------------------

    def list_application_environments(
        self, application_id: str, *, limit: Optional[int] = None, after: Optional[str] = None
    ) -> Page[ApplicationEnvironment]:
        path = f"/applications/{_seg(application_id, 'application_id')}/environments"
        return self._page(ApplicationEnvironment, path, limit, after)

    def iter_application_environments(
        self, application_id: str, *, limit: Optional[int] = None
    ) -> Iterator[ApplicationEnvironment]:
        _seg(application_id, "application_id")
        return _iterate(lambda **kw: self.list_application_environments(application_id, **kw), limit)

    def create_application_environment(
        self,
        application_id: str,
        environment_id: str,
        base_url: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> ApplicationEnvironment:
        """Places an application in an environment at a base URL."""
        path = f"/applications/{_seg(application_id, 'application_id')}/environments"
        body = {"environment_id": environment_id, "base_url": base_url}
        return self._one(ApplicationEnvironment, "POST", path, body=body, idempotency_key=idempotency_key)

    def delete_application_environment(self, application_id: str, application_environment_id: str) -> Deleted:
        path = (
            f"/applications/{_seg(application_id, 'application_id')}"
            f"/environments/{_seg(application_environment_id, 'application_environment_id')}"
        )
        return self._deleted(path)

    # -- environments --------------------------------------------------------

    def list_environments(self, *, limit: Optional[int] = None, after: Optional[str] = None) -> Page[Environment]:
        return self._page(Environment, "/environments", limit, after)

    def iter_environments(self, *, limit: Optional[int] = None) -> Iterator[Environment]:
        return _iterate(self.list_environments, limit)

    def get_environment(self, environment_id: str) -> Environment:
        return self._one(Environment, "GET", f"/environments/{_seg(environment_id, 'environment_id')}")

    def create_environment(
        self,
        name: str,
        domain: str,
        *,
        is_default: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
    ) -> Environment:
        body = _compact({"name": name, "domain": domain, "is_default": is_default})
        return self._one(Environment, "POST", "/environments", body=body, idempotency_key=idempotency_key)

    def update_environment(
        self,
        environment_id: str,
        *,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        is_default: Optional[bool] = None,
    ) -> Environment:
        """Not retried after a 5xx or a lost connection. The production
        environment is refused with ``protected``."""
        body = _compact({"name": name, "domain": domain, "is_default": is_default})
        return self._one(Environment, "PATCH", f"/environments/{_seg(environment_id, 'environment_id')}", body=body)

    def delete_environment(self, environment_id: str) -> Deleted:
        return self._deleted(f"/environments/{_seg(environment_id, 'environment_id')}")

    # -- credentials -------------------------------------------------------

    def list_credentials(
        self,
        *,
        application_environment_id: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
    ) -> Page[Credential]:
        """One page of runtime credentials: metadata only, never a secret."""
        return self._page(
            Credential, "/credentials", limit, after, application_environment_id=application_environment_id
        )

    def iter_credentials(
        self, *, application_environment_id: Optional[str] = None, limit: Optional[int] = None
    ) -> Iterator[Credential]:
        return _iterate(self.list_credentials, limit, application_environment_id=application_environment_id)

    def get_credential(self, credential_id: str) -> Credential:
        """A credential's metadata; ``client_secret`` is ``None``."""
        return self._one(Credential, "GET", f"/credentials/{_seg(credential_id, 'credential_id')}")

    def create_credential(
        self, application_environment_id: str, *, idempotency_key: Optional[str] = None
    ) -> Credential:
        """
        Issues a runtime credential for an application environment. The
        returned ``client_secret`` is shown this once. A retry of a request
        that already succeeded raises
        :class:`~end_point_blank.management.errors.IdempotencyReplayUnavailableError`
        rather than returning the secret again.
        """
        body = {"application_environment_id": application_environment_id}
        return self._one(Credential, "POST", "/credentials", body=body, idempotency_key=idempotency_key)

    def rotate_credential(self, credential_id: str, *, idempotency_key: Optional[str] = None) -> Credential:
        """Issues a new secret (returned once, as ``client_secret``); the old
        one keeps working for the grace window."""
        path = f"/credentials/{_seg(credential_id, 'credential_id')}/rotate"
        return self._one(Credential, "POST", path, idempotency_key=idempotency_key)

    def revoke_credential(self, credential_id: str) -> Deleted:
        """Revokes a credential (``DELETE /credentials/:id``): removed from
        intake, then deleted."""
        return self._deleted(f"/credentials/{_seg(credential_id, 'credential_id')}")


class ManagedClient(_OrganizationResources):
    """
    A provider's view of one of its managed clients (``managed: true``):
    the same application, environment, application environment and credential
    calls as :class:`ManagementClient`, sent under
    ``/api/v1/clients/<client_id>/...`` and acting on the client's
    organization. Every call answers ``not_found`` once the customer has
    claimed the client. Get one with :meth:`ManagementClient.for_managed_client`.
    """

    def __init__(self, transport: _Transport, client_id: str) -> None:
        self._transport = transport
        self.client_id = client_id
        self._prefix = f"/clients/{_seg(client_id, 'client_id')}"

    def __repr__(self) -> str:
        return f"<ManagedClient client_id={self.client_id!r}>"


class ManagementClient(_OrganizationResources):
    """
    A client for the EndPointBlank organization management API.

    ::

        from end_point_blank.management import ManagementClient

        mgmt = ManagementClient(key=os.environ["EPB_MANAGEMENT_KEY"])
        org = mgmt.get_organization()

    :param key: A management API key (``epb_mk_...``), created in the portal.
        Sent only as ``Authorization: Bearer <key>``, and never shown in an
        error, a log line or this object's ``repr``.
    :param base_url: The portal's URL (default ``https://app.endpointblank.com``).
        Requests go to ``<base_url>/api/v1/...``.
    :param max_retries: How many times one call is retried (default 2; ``0``
        turns retries off). A 429 is retried after its ``Retry-After``; a 5xx
        or a request that never got an answer is retried with backoff for
        GET, DELETE and POST (every POST carries an ``Idempotency-Key``, the
        same one on each retry), never for PATCH; a POST answered
        ``idempotency_request_in_progress`` is retried with the same key.
    :param max_retry_wait: The longest ``Retry-After`` (seconds) waited out
        before retrying (default 60). A longer one raises
        :class:`~end_point_blank.management.errors.RateLimitedError` at once.
    :param timeout: ``requests`` timeout: seconds, or ``(connect, read)``
        (default ``(3, 30)``).
    :param sleep: The function used to wait between retries (default
        :func:`time.sleep`); tests pass a stub.
    :param session: A ``requests.Session`` to send with. Defaults to the SDK's
        shared per-thread session.
    """

    def __init__(
        self,
        key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        max_retries: int = 2,
        max_retry_wait: float = 60.0,
        timeout: Timeout = _DEFAULT_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not isinstance(key, str) or not key.strip().startswith(KEY_PREFIX) or len(key.strip()) <= len(KEY_PREFIX):
            # The value is not repeated: a runtime client_secret pasted here by
            # mistake must not end up in a traceback.
            raise ConfigurationError(
                "ManagementClient needs a management API key, which starts with "
                f"'{KEY_PREFIX}' (create one in the EndPointBlank portal). Runtime "
                "client_id/client_secret credentials can't be used for the management API."
            )
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be an int >= 0")
        self.base_url = self._check_base_url(base_url)
        self._transport = _Transport(
            key.strip(),
            self.base_url,
            max_retries=max_retries,
            max_retry_wait=max_retry_wait,
            timeout=timeout,
            sleep=sleep,
            session=session,
            user_agent=f"end-point-blank-py/{VERSION} (management; python)",
        )
        self._prefix = ""

    @staticmethod
    def _check_base_url(base_url: str) -> str:
        if not isinstance(base_url, str):
            raise ValueError("base_url must be an http or https URL")
        parts = urlsplit(base_url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url must be an http or https URL")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("base_url must not carry userinfo, a query or a fragment")
        return base_url.strip().rstrip("/")

    def __repr__(self) -> str:
        return f"<ManagementClient base_url={self.base_url!r} key={KEY_PREFIX}…redacted>"

    @property
    def max_retries(self) -> int:
        return self._transport.max_retries

    def for_managed_client(self, client_id: str) -> ManagedClient:
        """The applications, environments and credentials of one of your
        managed clients, under ``/clients/<client_id>/...``."""
        return ManagedClient(self._transport, client_id)

    # -- organization --------------------------------------------------------

    def get_organization(self) -> Organization:
        """The organization the key belongs to, and the key's name and scope."""
        return self._one(Organization, "GET", "/organization")

    # -- API packages ------------------------------------------------------

    def list_api_packages(self, *, limit: Optional[int] = None, after: Optional[str] = None) -> Page[ApiPackage]:
        return self._page(ApiPackage, "/api_packages", limit, after)

    def iter_api_packages(self, *, limit: Optional[int] = None) -> Iterator[ApiPackage]:
        return _iterate(self.list_api_packages, limit)

    def get_api_package(self, api_package_id: str) -> ApiPackage:
        return self._one(ApiPackage, "GET", f"/api_packages/{_seg(api_package_id, 'api_package_id')}")

    def create_api_package(self, name: str, *, idempotency_key: Optional[str] = None) -> ApiPackage:
        return self._one(ApiPackage, "POST", "/api_packages", body={"name": name}, idempotency_key=idempotency_key)

    def update_api_package(self, api_package_id: str, *, name: str) -> ApiPackage:
        """Renames a package. Not retried after a 5xx or a lost connection."""
        path = f"/api_packages/{_seg(api_package_id, 'api_package_id')}"
        return self._one(ApiPackage, "PATCH", path, body={"name": name})

    def delete_api_package(self, api_package_id: str) -> Deleted:
        """Refused with ``api_package_assigned`` while a client holds it."""
        return self._deleted(f"/api_packages/{_seg(api_package_id, 'api_package_id')}")

    def list_package_endpoints(
        self, api_package_id: str, *, limit: Optional[int] = None, after: Optional[str] = None
    ) -> Page[PackageEndpoint]:
        """One page of what a package publishes."""
        path = f"/api_packages/{_seg(api_package_id, 'api_package_id')}/endpoints"
        return self._page(PackageEndpoint, path, limit, after)

    def iter_package_endpoints(self, api_package_id: str, *, limit: Optional[int] = None) -> Iterator[PackageEndpoint]:
        _seg(api_package_id, "api_package_id")
        return _iterate(lambda **kw: self.list_package_endpoints(api_package_id, **kw), limit)

    def add_package_endpoint(
        self,
        api_package_id: str,
        *,
        application_id: str,
        environment_id: str,
        endpoint_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> PackageEndpointAdded:
        """
        Adds one endpoint of an application (or, with ``endpoint_id``
        omitted, every endpoint of it) in one environment to a package. The
        answer's ``warnings`` lists client assignments of the package that
        now derive no grant.
        """
        path = f"/api_packages/{_seg(api_package_id, 'api_package_id')}/endpoints"
        body = _compact(
            {"application_id": application_id, "environment_id": environment_id, "endpoint_id": endpoint_id}
        )
        payload = self._call("POST", path, body=body, idempotency_key=idempotency_key)
        data = self._data(payload, "POST", path)
        return PackageEndpointAdded(
            endpoint=PackageEndpoint.from_dict(data),
            warnings=[ApiWarning.from_dict(w) for w in (payload.get("warnings") or [])],
        )

    def remove_package_endpoint(self, api_package_id: str, access_id: str) -> Deleted:
        """Removes a package entry (its ``id`` from :meth:`list_package_endpoints`)."""
        return self._deleted(
            f"/api_packages/{_seg(api_package_id, 'api_package_id')}/endpoints/{_seg(access_id, 'access_id')}"
        )

    # -- endpoint lookup ---------------------------------------------------

    def list_endpoints(
        self,
        *,
        application_id: Optional[str] = None,
        version: Optional[str] = None,
        limit: Optional[int] = None,
        after: Optional[str] = None,
    ) -> Page[Endpoint]:
        """The endpoints of your applications, optionally of one application
        and/or one deployed version name."""
        return self._page(Endpoint, "/endpoints", limit, after, application_id=application_id, version=version)

    def iter_endpoints(
        self, *, application_id: Optional[str] = None, version: Optional[str] = None, limit: Optional[int] = None
    ) -> Iterator[Endpoint]:
        return _iterate(self.list_endpoints, limit, application_id=application_id, version=version)

    # -- clients -----------------------------------------------------------

    def list_clients(self, *, limit: Optional[int] = None, after: Optional[str] = None) -> Page[Client]:
        """One page of clients (client invites, pending and accepted)."""
        return self._page(Client, "/clients", limit, after)

    def iter_clients(self, *, limit: Optional[int] = None) -> Iterator[Client]:
        return _iterate(self.list_clients, limit)

    def get_client(self, client_id: str) -> Client:
        """A client, with its contacts and pre-assignments."""
        return self._one(Client, "GET", f"/clients/{_seg(client_id, 'client_id')}")

    def create_client(
        self,
        name: str,
        *,
        contacts: Optional[Sequence[Mapping[str, Any]]] = None,
        packages: Optional[Sequence[Mapping[str, Any]]] = None,
        grants: Optional[Sequence[Mapping[str, Any]]] = None,
        managed: Optional[bool] = None,
        idempotency_key: Optional[str] = None,
    ) -> Client:
        """
        Invites a client (``POST /clients``). Share the returned
        ``invite_code`` with it.

        :param contacts: ``[{"email", "first_name", "last_name", "title"?,
            "phone_number"?}]`` -- your contacts to share with the client.
        :param packages: ``[{"api_package_id", "environment_id"}]`` to assign
            when the client accepts.
        :param grants: ``[{"target_application_id", "environment_id",
            "target_endpoint_id"?}]`` to grant when the client accepts.
        :param managed: ``True`` creates a managed client instead (see
            :meth:`create_managed_client`); it can't come with packages or
            grants.
        """
        body = _compact(
            {
                "name": name,
                "contacts": [dict(c) for c in contacts] if contacts is not None else None,
                "packages": [dict(p) for p in packages] if packages is not None else None,
                "grants": [dict(g) for g in grants] if grants is not None else None,
                "managed": managed,
            }
        )
        return self._one(Client, "POST", "/clients", body=body, idempotency_key=idempotency_key)

    def create_managed_client(
        self,
        name: str,
        *,
        contacts: Optional[Sequence[Mapping[str, Any]]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Client:
        """
        Creates a managed client (``POST /clients`` with ``managed: true``):
        its organization is created now and run by you until your customer
        claims it. Set it up with :meth:`for_managed_client`, assign it
        packages and grants like any accepted client, and invite the customer
        with :meth:`send_claim_invite`.
        """
        return self.create_client(name, contacts=contacts, managed=True, idempotency_key=idempotency_key)

    def delete_client(self, client_id: str) -> Deleted:
        """Removes a client. An unclaimed managed client that still holds
        credentials is refused with ``managed_client_has_credentials``."""
        return self._deleted(f"/clients/{_seg(client_id, 'client_id')}")

    def send_claim_invite(self, client_id: str, email: str, *, idempotency_key: Optional[str] = None) -> ClaimInvite:
        """Emails your customer an invite to claim a managed client."""
        path = f"/clients/{_seg(client_id, 'client_id')}/claim_invites"
        return self._one(ClaimInvite, "POST", path, body={"email": email}, idempotency_key=idempotency_key)

    # -- package assignments -------------------------------------------------

    def list_client_packages(
        self, client_id: str, *, limit: Optional[int] = None, after: Optional[str] = None
    ) -> Page[PackageAssignment]:
        path = f"/clients/{_seg(client_id, 'client_id')}/packages"
        return self._page(PackageAssignment, path, limit, after)

    def iter_client_packages(self, client_id: str, *, limit: Optional[int] = None) -> Iterator[PackageAssignment]:
        _seg(client_id, "client_id")
        return _iterate(lambda **kw: self.list_client_packages(client_id, **kw), limit)

    def assign_package(
        self,
        client_id: str,
        *,
        api_package_id: str,
        environment_id: str,
        idempotency_key: Optional[str] = None,
    ) -> PackageAssignment:
        """Assigns an API package to a client in one of your environments
        (``status`` ``"pending"`` for a client that has not accepted yet)."""
        path = f"/clients/{_seg(client_id, 'client_id')}/packages"
        body = {"api_package_id": api_package_id, "environment_id": environment_id}
        return self._one(PackageAssignment, "POST", path, body=body, idempotency_key=idempotency_key)

    def update_client_package(self, client_id: str, assignment_id: str, *, environment_id: str) -> PackageAssignment:
        """Moves an assignment to another environment. Not retried after a 5xx
        or a lost connection."""
        path = f"/clients/{_seg(client_id, 'client_id')}/packages/{_seg(assignment_id, 'assignment_id')}"
        return self._one(PackageAssignment, "PATCH", path, body={"environment_id": environment_id})

    def unassign_package(self, client_id: str, assignment_id: str) -> Deleted:
        return self._deleted(
            f"/clients/{_seg(client_id, 'client_id')}/packages/{_seg(assignment_id, 'assignment_id')}"
        )

    # -- direct grants -------------------------------------------------------

    def list_client_grants(
        self, client_id: str, *, limit: Optional[int] = None, after: Optional[str] = None
    ) -> Page[Grant]:
        path = f"/clients/{_seg(client_id, 'client_id')}/grants"
        return self._page(Grant, path, limit, after)

    def iter_client_grants(self, client_id: str, *, limit: Optional[int] = None) -> Iterator[Grant]:
        _seg(client_id, "client_id")
        return _iterate(lambda **kw: self.list_client_grants(client_id, **kw), limit)

    def create_client_grant(
        self,
        client_id: str,
        *,
        target_application_id: str,
        environment_id: str,
        target_endpoint_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Grant:
        """Grants the client (the source) access to one of your applications
        (the target), or one endpoint of it, in one environment."""
        path = f"/clients/{_seg(client_id, 'client_id')}/grants"
        body = _compact(
            {
                "target_application_id": target_application_id,
                "environment_id": environment_id,
                "target_endpoint_id": target_endpoint_id,
            }
        )
        return self._one(Grant, "POST", path, body=body, idempotency_key=idempotency_key)

    def delete_client_grant(self, client_id: str, grant_id: str) -> Deleted:
        """Revokes a direct grant; ``still_granted_by_package`` says whether a
        package the client holds keeps it granted."""
        return self._deleted(f"/clients/{_seg(client_id, 'client_id')}/grants/{_seg(grant_id, 'grant_id')}")


__all__ = ["DEFAULT_BASE_URL", "ManagedClient", "ManagementClient"]
