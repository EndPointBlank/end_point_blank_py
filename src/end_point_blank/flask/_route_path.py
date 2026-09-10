from __future__ import annotations

from ..commands.route_path import normalize_route_pattern


def route_path(request) -> str:
    """
    The path both Flask decorators send to intake.

    The matched rule, normalized (``/students/<int:student_id>`` becomes
    ``/students/{student_id}``), or the concrete request path when nothing
    matched -- a 404 or a manually pushed request context has no ``url_rule``,
    and sending the raw path is better than sending nothing.
    """
    url_rule = getattr(request, "url_rule", None)
    if url_rule is None:
        return request.path
    return normalize_route_pattern(str(url_rule))
