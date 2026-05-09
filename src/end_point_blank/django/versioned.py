from __future__ import annotations

from typing import List


def versioned(versions: List[str], state: str = "__default__"):
    """
    Django view decorator that attaches API version metadata to a view function.

    Used by :func:`~end_point_blank.django.endpoint_registrar.register_django_endpoints`
    when publishing endpoint information to the EndPointBlank API::

        @versioned(["v1", "v2"], state="Current")
        def user_list(request):
            ...

    :param versions: List of version strings (e.g. ``["v1", "v2"]``).
    :param state: Lifecycle state label (e.g. ``"Current"``, ``"Deprecated"``).
    """
    def decorator(func):
        if not hasattr(func, "_epb_versions"):
            func._epb_versions = {}
        func._epb_versions[state] = list(versions)
        return func

    return decorator
