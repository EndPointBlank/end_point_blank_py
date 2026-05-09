import threading
import uuid as _uuid_mod
from typing import Any, Optional

_local = threading.local()


class RequestStore:
    """
    Thread-local store for the current WSGI environ dict.

    Equivalent to the Ruby gem's ``Rack::EnvStore``, which stores the Rack
    env hash in a thread-local variable so that any code running in the same
    request thread can access request details without explicit parameter passing.

    Set by :class:`~end_point_blank.middleware.ReportInteractionMiddleware`
    on every request.
    """

    @staticmethod
    def set(environ: dict) -> None:
        _local.environ = environ
        _local.uuid = environ.get("HTTP_X_REQUEST_ID") or str(_uuid_mod.uuid4())

    @staticmethod
    def get() -> Optional[dict]:
        return getattr(_local, "environ", None)

    @staticmethod
    def get_uuid() -> Optional[str]:
        return getattr(_local, "uuid", None)

    @staticmethod
    def set_source_application_environment_id(id: str) -> None:
        _local.source_application_environment_id = id

    @staticmethod
    def get_source_application_environment_id() -> Optional[str]:
        return getattr(_local, "source_application_environment_id", None)

    @staticmethod
    def clear() -> None:
        _local.environ = None
        _local.uuid = None
        _local.source_application_environment_id = None
