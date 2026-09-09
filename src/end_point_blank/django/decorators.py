from __future__ import annotations

import functools
import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

_DJANGO_PARAM_RE = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


def _route_path(request) -> str:
    """Return the normalized route pattern (e.g. /classes/{class_id}/students)."""
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match and resolver_match.route:
        normalized = _DJANGO_PARAM_RE.sub(r"{\1}", resolver_match.route)
        return "/" + normalized.lstrip("/")
    return request.path


def authenticated(view_func: Callable) -> Callable:
    """
    Django view decorator that enforces EndPointBlank authentication.

    Equivalent to including ``EndPointBlank::Rails::Authenticated`` in a Rails controller::

        @authenticated
        def my_view(request):
            return HttpResponse("Hello")

    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        from ..commands.basic_authenticate import BasicAuthenticate
        from ..commands.version_finder import VersionFinder
        from ..unauthorized_error import refusal_from

        environ = request.environ
        version = VersionFinder().find(environ)
        response = BasicAuthenticate.authenticate(environ, _route_path(request), version)

        if response is None or response.status_code != 201:
            raise refusal_from(response, "Authentication")

        return view_func(request, *args, **kwargs)

    return wrapper


def authorized(view_func: Callable) -> Callable:
    """
    Django view decorator that enforces EndPointBlank authorization.

    Equivalent to including ``EndPointBlank::Rails::Authorized`` in a Rails controller::

        @authorized
        def my_view(request):
            return HttpResponse("Hello")

    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        from ..commands.endpoint_authorize import EndpointAuthorize
        from ..commands.version_finder import VersionFinder
        from ..unauthorized_error import refusal_from

        environ = request.environ
        version = VersionFinder().find(environ)
        response = EndpointAuthorize.authorize(environ, _route_path(request), version)

        if response is None or response.status_code != 201:
            raise refusal_from(response, "Authorization")

        return view_func(request, *args, **kwargs)

    return wrapper
