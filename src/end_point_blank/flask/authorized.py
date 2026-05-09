from __future__ import annotations

import functools
import logging
import re
import time
from typing import Callable

logger = logging.getLogger(__name__)

_FLASK_PARAM_RE = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


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
        from ..unauthorized_error import UnauthorizedError

        t0 = time.monotonic()
        environ = request.environ
        url_rule = request.url_rule
        path = _FLASK_PARAM_RE.sub(r"{\1}", str(url_rule)) if url_rule else request.path
        logger.debug("[authorized] calling EndpointAuthorize.authorize for %s", path)

        response = EndpointAuthorize.authorize(environ, path, version=VersionFinder().find(environ))
        logger.debug("[authorized] authorize returned in %.3fs", time.monotonic() - t0)

        if response is None or response.status_code != 201:
            error_msg = "Authorization service unavailable"
            if response is not None:
                try:
                    error_msg = response.json().get("error", response.text)
                except Exception:
                    error_msg = response.text
            raise UnauthorizedError(f"Authorization failed: {error_msg}")

        logger.debug("[authorized] calling view function (%.3fs)", time.monotonic() - t0)
        result = func(*args, **kwargs)
        logger.debug("[authorized] view function returned (%.3fs)", time.monotonic() - t0)
        return result

    return wrapper
