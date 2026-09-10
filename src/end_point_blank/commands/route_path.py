from __future__ import annotations

import re

# Flask and Django spell a URL converter the same way -- ``<converter:name>``,
# with the converter optional -- so one expression serves both integrations.
_PARAM_RE = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


def normalize_route_pattern(pattern: str) -> str:
    """
    Render a framework route pattern the way an endpoint path is stored.

    ``/students/<int:student_id>`` becomes ``/students/{student_id}``, and the
    result always carries a leading slash (Django's ``resolver_match.route``
    does not).

    This is the SDK's only spelling of that conversion, deliberately. Intake's
    ``Intake.Apis.get_endpoint/3`` matches ``path`` exactly, so a caller that
    normalizes differently from the registrar that published the endpoint --
    or differently from its own sibling decorator -- resolves to no row at all,
    and is refused for a grant it actually holds. Every second spelling of this
    function is that bug waiting to be reintroduced.
    """
    return "/" + _PARAM_RE.sub(r"{\1}", pattern).lstrip("/")
