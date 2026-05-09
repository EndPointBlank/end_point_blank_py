from __future__ import annotations

import logging
from typing import Callable

from ..commands.version_finder import VersionFinder
from ..request_store import RequestStore
from ..unauthorized_error import UnauthorizedError
from ..writers.writer import Writer

logger = logging.getLogger(__name__)


class ReportInteractionMiddleware:
    """
    Django middleware that stores the current request in a thread-local and
    reports unhandled application errors to the EndPointBlank API.

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
        self._writer = Writer("application_errors_url")
        self._version_finder = VersionFinder()

    def __call__(self, request):
        RequestStore.set(request.environ)
        try:
            response = self.get_response(request)
            return response
        except UnauthorizedError:
            raise
        except Exception as exc:
            self._report_error(request, exc)
            raise
        finally:
            RequestStore.clear()

    def _report_error(self, request, exc: Exception) -> None:
        try:
            environ = request.environ
            version = self._version_finder.find(environ)
            if version is None:
                return

            headers = {k: v for k, v in request.headers.items()}

            self._writer.write(
                message=str(exc),
                exc=exc,
                status=500,
                headers=headers,
                path=request.path,
                action=request.method,
                version=version,
            )
        except Exception as reporting_exc:
            logger.error("Failed to report error to EndPointBlank: %s", reporting_exc)
