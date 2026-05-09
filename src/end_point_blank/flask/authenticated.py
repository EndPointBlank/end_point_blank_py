from __future__ import annotations

import functools
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)


def authenticated(func: Callable) -> Callable:
    """
    Flask route decorator that enforces EndPointBlank authentication before
    the view function is invoked.

    If the remote authentication service does not return HTTP 201, an
    :class:`~end_point_blank.unauthorized_error.UnauthorizedError` is raised.

    Equivalent to including the ``EndPointBlank::Rails::Authenticated`` concern
    in a Rails controller::

        @app.route("/protected")
        @authenticated
        def protected_view():
            return "Hello, authenticated user!"

    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        from flask import request
        from ..commands.basic_authenticate import BasicAuthenticate
        from ..commands.version_finder import VersionFinder
        from ..unauthorized_error import UnauthorizedError

        environ = request.environ
        path = request.path
        version = VersionFinder().find(environ)

        response = BasicAuthenticate.authenticate(environ, path, version)

        if response is None or response.status_code != 201:
            error_msg = "Authentication service unavailable"
            if response is not None:
                try:
                    error_msg = response.json().get("error", response.text)
                except Exception:
                    error_msg = response.text
            raise UnauthorizedError(f"Authentication failed: {error_msg}")

        return func(*args, **kwargs)

    return wrapper
