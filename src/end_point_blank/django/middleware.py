from __future__ import annotations

import logging
from typing import Callable

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
        RequestWriter.write()

        status = None
        headers: dict = {}
        body = None

        try:
            response = self.get_response(request)
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


def _response_body(response) -> str | None:
    content = getattr(response, "content", None)
    if content is None:
        return None
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return None
