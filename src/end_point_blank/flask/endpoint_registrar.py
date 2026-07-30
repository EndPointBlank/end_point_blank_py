from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from ..commands.endpoint_update import EndpointUpdate

logger = logging.getLogger(__name__)


def register_flask_endpoints(flask_app) -> None:
    """
    Inspects all registered Flask URL rules and publishes the endpoint list
    to the EndPointBlank API via :class:`~end_point_blank.commands.endpoint_update.EndpointUpdate`.

    Equivalent to the Rails route-scanning logic in the Ruby gem's
    ``EndPointBlank::Commands::EndpointUpdate#endpoints``.

    Call this once after your Flask app is fully configured, e.g. inside an
    ``app.before_first_request`` handler or at startup::

        from end_point_blank.flask import register_flask_endpoints

        with app.app_context():
            register_flask_endpoints(app)

    :param flask_app: A :class:`flask.Flask` application instance.
    """
    endpoints = _collect_endpoints(flask_app)
    EndpointUpdate.send_update(endpoints)
    logger.info("Registered %d endpoints with EndPointBlank.", len(endpoints))


def _collect_endpoints(flask_app) -> List[Dict[str, Any]]:
    endpoints = []
    for rule in flask_app.url_map.iter_rules():
        # Skip Flask internals
        if rule.endpoint in ("static",):
            continue

        view_func = flask_app.view_functions.get(rule.endpoint)
        versions = getattr(view_func, "_epb_versions", [])

        for method in rule.methods or []:
            if method in ("HEAD", "OPTIONS"):
                continue
            endpoints.append({
                "path": _normalize_path(str(rule.rule)),
                "http_method": method,
                "endpoint_versions": versions,
            })

    return endpoints


def _normalize_path(path: str) -> str:
    """Convert Flask path syntax to Rails-style: <int:id> -> {id}, <id> -> {id}."""
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)
