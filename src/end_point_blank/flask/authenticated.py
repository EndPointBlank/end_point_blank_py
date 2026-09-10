from __future__ import annotations

import functools
import json
import logging
from typing import Callable

from ._route_path import route_path

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
        from ..unauthorized_error import refusal_from

        environ = request.environ
        # The route pattern, not the concrete URL. Intake resolves the endpoint
        # row by an exact match on path, so "/students/42" resolves to nothing
        # and the caller is refused for a grant they hold. Shared with
        # @authorized so the two cannot drift apart again.
        path = route_path(request)
        version = VersionFinder().find(environ)

        response = BasicAuthenticate.authenticate(environ, path, version)

        if response is None or response.status_code != 201:
            raise refusal_from(response, "Authentication")

        return func(*args, **kwargs)

    return wrapper
