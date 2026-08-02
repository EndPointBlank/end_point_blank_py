from __future__ import annotations

import io
import logging
from typing import Callable

from .. import deprecation_headers
from ..request_store import RequestStore
from ..unauthorized_error import UnauthorizedError
from ..writers.exception_writer import ExceptionWriter
from ..writers.request_writer import RequestWriter
from ..writers.response_writer import ResponseWriter

logger = logging.getLogger(__name__)


class ReportInteractionMiddleware:
    """
    Django middleware that stores the current request in a thread-local,
    writes request/response payloads, and reports unhandled application
    errors to the EndPointBlank API.

    :class:`~end_point_blank.unauthorized_error.UnauthorizedError` is re-raised
    without logging, as unauthorized access is expected behavior.

    Equivalent to the Ruby gem's ``EndPointBlank::Middleware::Rack::ReportInteraction``.

    Register in ``settings.py``::

        MIDDLEWARE = [
            "end_point_blank.django.ReportInteractionMiddleware",
            ...
        ]
    """

    def __init__(self, get_response: Callable) -> None:
        self.get_response = get_response

    def __call__(self, request):
        RequestStore.set(request.environ)

        # Django's WSGIRequest captures self._stream wrapping
        # environ["wsgi.input"] at __init__ time. RequestWriter._read_body
        # below drains that input to record the request payload, and then
        # replaces the environ key — but Django's _stream still references
        # the now-drained source. The view's subsequent request.body /
        # json.loads(request.body) reads return b"" and any "name is
        # required"-style POST handler trips on what looks like an empty
        # body.
        #
        # Prime request.body so Django caches the bytes in self._body,
        # then drop the cached bytes back into environ so RequestWriter
        # has something to read non-destructively.
        try:
            cached_body = request.body
        except Exception:
            cached_body = b""

        request.environ["wsgi.input"] = io.BytesIO(cached_body)

        RequestWriter.write()

        status = None
        headers: dict = {}
        body = None

        try:
            response = self.get_response(request)
            _apply_deprecation_headers(response)
            status = getattr(response, "status_code", None)
            try:
                headers = {k: v for k, v in response.items()}
            except Exception:
                headers = {}
            body = _response_body(response)
            return response
        except UnauthorizedError:
            raise
        except Exception as exc:
            # The exception will be rendered by an outer middleware (e.g.
            # JsonErrorMiddleware) which sits outside this one, so we never
            # see the rendered status / body. Synthesize them so the response
            # row still gets recorded — intake requires a non-nil status.
            if status is None:
                status = 500
            if body is None:
                body = f"{exc.__class__.__name__}: {exc}"
            ExceptionWriter.write(exc)
            raise
        finally:
            ResponseWriter.write(status=status, headers=headers, body=body)
            RequestStore.clear()

    def process_exception(self, request, exception):
        # Django intercepts view exceptions and runs process_exception hooks
        # before the exception can propagate to our __call__ rescue. Fire
        # ExceptionWriter here so the application_errors event lands even
        # when an outer middleware (e.g. a JSON error renderer) converts
        # the exception into a normal response.
        if isinstance(exception, UnauthorizedError):
            return None
        ExceptionWriter.write(exception)
        return None


def _apply_deprecation_headers(response) -> None:
    """Adds the RFC 9745 ``Deprecation`` and RFC 8594 ``Sunset`` headers.

    Django hands back a response object rather than the ``(status, headers)``
    pair the WSGI middleware intercepts at ``start_response``, so the headers go
    on here instead — but the rule is the same one
    :func:`end_point_blank.deprecation_headers.apply` follows: never overwrite a
    header the application already set, and never raise into a response that has
    already succeeded.
    """
    try:
        deprecation = RequestStore.get_deprecation()
        if not deprecation:
            return
        for name, value in deprecation_headers.build(deprecation).items():
            if not response.has_header(name):
                response[name] = value
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to apply deprecation headers: %s", exc)


def _response_body(response) -> str | None:
    content = getattr(response, "content", None)
    if content is None:
        return None
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return None
