from __future__ import annotations

from typing import List


def versioned(versions: List[str]):
    """
    Django view decorator that records which API versions a view serves.

    Used by :func:`~end_point_blank.django.endpoint_registrar.register_django_endpoints`
    when publishing endpoint information to the EndPointBlank API.

    Lifecycle state (Current, Deprecated, ...) is **not** declared here. It is
    managed in the EndPointBlank portal, where changing it does not require
    shipping code. This reports which versions exist, not what they mean::

        @versioned(["v1", "v2"])
        def user_list(request):
            ...

    Stacked decorators merge, deduplicated, in declaration order.

    :param versions: List of version strings (e.g. ``["v1", "v2"]``).
    """
    def decorator(func):
        existing = getattr(func, "_epb_versions", [])
        func._epb_versions = list(dict.fromkeys([*existing, *versions]))
        return func

    return decorator
