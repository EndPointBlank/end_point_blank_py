import threading
import uuid as _uuid_mod
from typing import Optional

_local = threading.local()

# Keys for values stored inside the WSGI environ. Namespaced so they cannot
# collide with anything the server or another middleware puts there.
_UUID_KEY = "end_point_blank.uuid"
_SOURCE_ENV_ID_KEY = "end_point_blank.source_application_environment_id"
_DEPRECATION_KEY = "end_point_blank.deprecation"


class RequestStore:
    """
    Per-request store, keyed off the WSGI environ.

    Only the environ itself is thread-local; everything derived from a request
    lives *inside* that environ. That distinction is the point.

    WSGI servers reuse threads between requests, so a thread-local is correct
    only for as long as something reliably clears it — here, the middleware's
    ``finally``. Anything setting a value without that middleware in the stack
    would strand it on the thread, and the next request served by that thread
    could read the previous caller's data: their app-environment id on our audit
    rows, their sunset date on our response.

    The environ is per-request by construction. The server builds a fresh dict
    per request and :meth:`set` replaces it wholesale, so a stranded value is
    unreachable even if :meth:`clear` never runs — correctness stops depending
    on cleanup happening.

    Outside a request there is nowhere to put anything, so the setters are
    no-ops and the getters return ``None``.

    Set by :class:`~end_point_blank.middleware.ReportInteractionMiddleware` (and
    the Django middleware) on every request.
    """

    @staticmethod
    def set(environ: dict) -> None:
        _local.environ = environ
        if environ is not None:
            environ[_UUID_KEY] = environ.get("HTTP_X_REQUEST_ID") or str(_uuid_mod.uuid4())

    @staticmethod
    def get() -> Optional[dict]:
        return getattr(_local, "environ", None)

    @staticmethod
    def get_uuid() -> Optional[str]:
        return RequestStore._fetch(_UUID_KEY)

    @staticmethod
    def set_source_application_environment_id(id: str) -> None:
        """The calling service's application environment, resolved by
        ``authorized`` and stamped onto every audit row for this request."""
        RequestStore._put(_SOURCE_ENV_ID_KEY, id)

    @staticmethod
    def get_source_application_environment_id() -> Optional[str]:
        return RequestStore._fetch(_SOURCE_ENV_ID_KEY)

    @staticmethod
    def set_deprecation(deprecation: Optional[dict]) -> None:
        """The authorize response's deprecation block, stashed on the way in so
        the middleware can turn it into ``Deprecation`` and ``Sunset`` headers
        on the way out."""
        RequestStore._put(_DEPRECATION_KEY, deprecation)

    @staticmethod
    def get_deprecation() -> Optional[dict]:
        return RequestStore._fetch(_DEPRECATION_KEY)

    @staticmethod
    def clear() -> None:
        # Dropping the environ drops everything stored in it, so this does not
        # need to know which keys exist — a new value would otherwise be one
        # more thing to remember to clear, which is how the bug comes back.
        _local.environ = None

    @staticmethod
    def _put(key: str, value) -> None:
        environ = RequestStore.get()
        if environ is not None:
            environ[key] = value

    @staticmethod
    def _fetch(key: str):
        environ = RequestStore.get()
        return environ.get(key) if environ is not None else None
