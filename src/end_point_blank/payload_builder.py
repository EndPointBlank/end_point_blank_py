from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional
from io import BytesIO

from .configuration import Configuration
from .request_store import RequestStore
from .session_configuration import SessionConfiguration


class PayloadBuilder:
    """
    Builds the payload dict sent to the EndPointBlank API for error reporting.

    Equivalent to the Ruby gem's ``EndPointBlank::PayloadBuilder``.
    """

    @classmethod
    def build(
        cls,
        *,
        message: str,
        stacktrace: Optional[List[str]] = None,
        exc: Optional[BaseException] = None,
        status: int,
        headers: Optional[Dict[str, str]] = None,
        sent_at: Optional[datetime] = None,
        path: Optional[str] = None,
        action: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        environ = RequestStore.get()

        if exc is not None and stacktrace is None:
            stacktrace = traceback.format_tb(exc.__traceback__)

        return {
            "message": message,
            "url": cls._build_url(environ),
            "stacktrace": "\n".join(stacktrace) if stacktrace else None,
            "status": status,
            "app_name": Configuration().app_name,
            "request_headers": headers or {},
            "env": SessionConfiguration.env_name(),
            "path": path,
            "action": action,
            "endpoint_version": version,
            "request": cls._read_body(environ),
            "sent_at": (sent_at or datetime.utcnow()).isoformat(),
        }

    @staticmethod
    def _build_url(environ: Optional[dict]) -> Optional[str]:
        if not environ:
            return None
        scheme = environ.get("wsgi.url_scheme", "http")
        host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "localhost")
        path = environ.get("PATH_INFO", "")
        query = environ.get("QUERY_STRING", "")
        url = f"{scheme}://{host}{path}"
        if query:
            url += f"?{query}"
        return url

    @staticmethod
    def _read_body(environ: Optional[dict]) -> Optional[str]:
        if not environ:
            return None
        wsgi_input = environ.get("wsgi.input")
        if not wsgi_input:
            return None
        try:
            body = wsgi_input.read()
            # Restore the stream so the application can still read it
            environ["wsgi.input"] = BytesIO(body)
            return body.decode("utf-8", errors="replace") if body else None
        except Exception:
            return None
