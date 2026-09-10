from __future__ import annotations

import functools
import logging
import time
from typing import Callable

from ._route_path import route_path

logger = logging.getLogger(__name__)


def authorized(func: Callable) -> Callable:
    """
    Flask route decorator that enforces EndPointBlank authorization before
    the view function is invoked.

    If the remote authorization service does not return HTTP 201, an
    :class:`~end_point_blank.unauthorized_error.UnauthorizedError` is raised.

    Equivalent to including the ``EndPointBlank::Rails::Authorized`` concern
    in a Rails controller::

        @app.route("/sensitive")
        @authorized
        def sensitive_view():
            return "Hello, authorized user!"

    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        from flask import request
        from ..commands.endpoint_authorize import EndpointAuthorize
        from ..commands.version_finder import VersionFinder
        from ..unauthorized_error import refusal_from

        t0 = time.monotonic()
        environ = request.environ
        path = route_path(request)
        logger.debug("[authorized] calling EndpointAuthorize.authorize for %s", path)

        response = EndpointAuthorize.authorize(environ, path, version=VersionFinder().find(environ))
        logger.debug("[authorized] authorize returned in %.3fs", time.monotonic() - t0)

        if response is None or response.status_code != 201:
            raise refusal_from(response, "Authorization")

        logger.debug("[authorized] calling view function (%.3fs)", time.monotonic() - t0)
        result = func(*args, **kwargs)
        logger.debug("[authorized] view function returned (%.3fs)", time.monotonic() - t0)
        return result

    return wrapper
