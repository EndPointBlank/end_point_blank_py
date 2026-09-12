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

_FLASK_EXCEPTION_REPORTED_KEY = "end_point_blank.flask_exception_reported"


def _report_flask_exception(_sender: Any, exception: Exception, **_extra: Any) -> None:
    """Report Flask exceptions that Flask converts into a normal 500 response.

    The WSGI middleware only sees the exception when Flask is configured to
    propagate it. The signal fires before Flask handles it, while RequestStore
    still points at the current request, so it covers Flask's default production
    behavior too.
    """
    environ = RequestStore.get()
    if environ is None or isinstance(exception, UnauthorizedError):
        return
    # Keyed on the identity of the exception that was reported, not a bare
    # "something was reported this request" flag. A custom error handler
    # invoked for *this* exception can itself raise a different exception,
    # which Flask does not route back through this signal — it propagates
    # straight out to the WSGI layer below. A boolean flag would make
    # _flask_exception_was_reported() true for that second, distinct
    # exception and silently drop it, even though it is the one that
    # actually reached the client. Storing id(exception) means the guard
    # only ever suppresses a genuine re-delivery of this same exception
    # object — cheap, and avoids keeping the exception (and its traceback)
    # alive in the environ for the rest of the request.
    if environ.get(_FLASK_EXCEPTION_REPORTED_KEY) == id(exception):
        return

    ExceptionWriter.write(exception)
    environ[_FLASK_EXCEPTION_REPORTED_KEY] = id(exception)


def _connect_flask_signal() -> None:
    """Best-effort subscribe to Flask's ``got_request_exception`` signal.

    No-ops (leaving the middleware a plain WSGI integration) in two distinct
    failure modes:

    * Flask is not installed at all — ``ImportError`` on the import below.
    * Flask *is* installed but older than 2.3 without blinker present. The
      import above still succeeds — ``got_request_exception`` exists as a
      no-op fake signal — but calling ``.connect()`` on it raises
      ``RuntimeError("Signalling support is unavailable because the blinker
      library is not installed.")``.

    flask is not a base dependency of this package (it is only pulled in,
    at ``>=2.3``, by the optional ``flask`` extra), so nothing stops a host
    from pairing this SDK with an older Flask without blinker; importing
    this module must not crash that host's boot over a feature it may not
    even use.
    """
    try:
        from flask import got_request_exception

        got_request_exception.connect(_report_flask_exception, weak=False)
    except (ImportError, RuntimeError):
        pass


_connect_flask_signal()


def _flask_exception_was_reported(exception: BaseException) -> bool:
    environ = RequestStore.get()
    return bool(environ) and environ.get(_FLASK_EXCEPTION_REPORTED_KEY) == id(exception)


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
        except UnauthorizedError as exc:
            # Same gap the Django middleware has: a refusal that propagates
            # never reaches start_response, so status_holder stays None and the
            # response row goes to intake with a null status. The refusing
            # status is on the exception, so record that.
            if status_holder[0] is None:
                status_holder[0] = exc.status_code
            raise
        except Exception as exc:
            if not _flask_exception_was_reported(exc):
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
