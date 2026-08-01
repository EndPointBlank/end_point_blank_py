from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..request_store import RequestStore
from .. import deprecation_headers
from ..unauthorized_error import UnauthorizedError
from ..writers.exception_writer import ExceptionWriter
from ..writers.request_writer import RequestWriter
from ..writers.response_writer import ResponseWriter

logger = logging.getLogger(__name__)


class ReportInteractionMiddleware:
    """
    WSGI middleware that stores the current request environ in a thread-local,
    writes request/response payloads, and reports unhandled application errors
    to the EndPointBlank API.

    :class:`~end_point_blank.unauthorized_error.UnauthorizedError` is re-raised
    without logging, as unauthorized access is expected behavior.

    Equivalent to the Ruby gem's
    ``EndPointBlank::Middleware::Rack::ReportInteraction``.

    Wrap your WSGI application at startup::

        from end_point_blank.middleware import ReportInteractionMiddleware

        app = ReportInteractionMiddleware(app)

    For Flask::

        from end_point_blank.middleware import ReportInteractionMiddleware

        app.wsgi_app = ReportInteractionMiddleware(app.wsgi_app)

    For Django (``settings.py``)::

        MIDDLEWARE = [
            "end_point_blank.django.ReportInteractionMiddleware",
            ...
        ]
    """

    def __init__(self, app: Callable) -> None:
        self._app = app

    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "?")
        path = environ.get("PATH_INFO", "?")
        t0 = time.monotonic()
        logger.debug("[middleware] >>> %s %s — entered", method, path)

        RequestStore.set(environ)
        logger.debug("[middleware] RequestStore.set done (%.3fs)", time.monotonic() - t0)

        status_holder: List[Optional[int]] = [None]
        headers_holder: List[Dict[str, str]] = [{}]

        def wrapped_start_response(
            status: str,
            response_headers: List[Tuple[str, str]],
            exc_info: Any = None,
        ) -> Callable:
            try:
                status_holder[0] = int(status.split(" ", 1)[0])
            except (ValueError, IndexError):
                status_holder[0] = None
            # RFC 9745 / RFC 8594. start_response is the only point at which
            # the response headers exist and have not yet been sent — the
            # `finally` below runs after the app has produced them, too late to
            # add any.
            response_headers = deprecation_headers.apply(
                response_headers, RequestStore.get_deprecation()
            )

            headers_holder[0] = dict(response_headers)
            if exc_info is not None:
                return start_response(status, response_headers, exc_info)
            return start_response(status, response_headers)

        try:
            logger.debug("[middleware] calling RequestWriter.write (%.3fs)", time.monotonic() - t0)
            RequestWriter.write()
            logger.debug("[middleware] RequestWriter.write done (%.3fs)", time.monotonic() - t0)

            logger.debug("[middleware] calling Flask app (%.3fs)", time.monotonic() - t0)
            result = self._app(environ, wrapped_start_response)
            logger.debug("[middleware] Flask app returned (%.3fs)", time.monotonic() - t0)
            return result
        except UnauthorizedError:
            raise
        except Exception as exc:
            ExceptionWriter.write(exc)
            raise
        finally:
            logger.debug("[middleware] calling ResponseWriter.write (%.3fs)", time.monotonic() - t0)
            ResponseWriter.write(
                status=status_holder[0],
                headers=headers_holder[0],
                body=None,
            )
            logger.debug("[middleware] <<< %s %s done (%.3fs)", method, path, time.monotonic() - t0)
            RequestStore.clear()


def _extract_headers(environ: Dict[str, Any]) -> Dict[str, str]:
    """Extracts HTTP headers from a WSGI environ dict."""
    headers: Dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            header_name = key[5:].replace("_", "-").title()
            headers[header_name] = value
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            header_name = key.replace("_", "-").title()
            headers[header_name] = value
    return headers
