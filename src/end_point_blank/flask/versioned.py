from __future__ import annotations

from typing import List, Optional


def versioned(versions: List[str], state: str = "__default__"):
    """
    Flask route decorator that attaches API version metadata to a view function.

    The metadata is used by
    :func:`~end_point_blank.flask.endpoint_registrar.register_flask_endpoints`
    when publishing endpoint information to the EndPointBlank API.

    Equivalent to the ``version`` DSL in the Ruby gem's
    ``EndPointBlank::Rails::Versioned`` concern::

        @app.route("/api/users")
        @versioned(["v1", "v2"], state="Current")
        def list_users():
            return []

        @app.route("/api/users/<int:user_id>")
        @versioned(["v1"], state="Deprecated")
        def get_user(user_id):
            ...

    Multiple ``@versioned`` decorators can be stacked on the same function.

    :param versions: List of version strings (e.g. ``["v1", "v2"]``).
    :param state: Lifecycle state label (e.g. ``"Current"``, ``"Deprecated"``).
    """
    def decorator(func):
        if not hasattr(func, "_epb_versions"):
            func._epb_versions = {}
        func._epb_versions[state] = list(versions)
        return func

    return decorator
