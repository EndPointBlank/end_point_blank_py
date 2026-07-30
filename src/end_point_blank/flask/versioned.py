from __future__ import annotations

from typing import List


def versioned(versions: List[str]):
    """
    Flask route decorator that records which API versions a view serves.

    The metadata is used by
    :func:`~end_point_blank.flask.endpoint_registrar.register_flask_endpoints`
    when publishing endpoint information to the EndPointBlank API.

    Lifecycle state (Current, Deprecated, ...) is **not** declared here. It is
    managed in the EndPointBlank portal, where changing it does not require
    shipping code. This reports which versions exist, not what they mean::

        @app.route("/api/users")
        @versioned(["v1", "v2"])
        def list_users():
            return []

    Multiple ``@versioned`` decorators can be stacked on the same function; the
    versions merge, deduplicated, in declaration order.

    :param versions: List of version strings (e.g. ``["v1", "v2"]``).
    """
    def decorator(func):
        existing = getattr(func, "_epb_versions", [])
        # dict.fromkeys rather than set(): deduplicates while preserving
        # declaration order, so the manifest stays stable between deploys
        # instead of churning.
        func._epb_versions = list(dict.fromkeys([*existing, *versions]))
        return func

    return decorator
