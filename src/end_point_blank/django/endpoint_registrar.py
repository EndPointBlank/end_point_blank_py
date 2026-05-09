from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..commands.endpoint_update import EndpointUpdate

logger = logging.getLogger(__name__)


def register_django_endpoints() -> None:
    """
    Inspects all registered Django URL patterns and publishes the endpoint list
    to the EndPointBlank API.

    Equivalent to the Rails route-scanning logic in the Ruby gem.
    Call once after Django is fully initialized, e.g. in ``AppConfig.ready()``::

        from end_point_blank.django import register_django_endpoints

        class MyAppConfig(AppConfig):
            def ready(self):
                register_django_endpoints()

    """
    from django.urls import get_resolver

    endpoints = _collect_endpoints(get_resolver())
    EndpointUpdate.send_update(endpoints)
    logger.info("Registered %d endpoints with EndPointBlank.", len(endpoints))


def _collect_endpoints(resolver, prefix: str = "") -> List[Dict[str, Any]]:
    endpoints = []
    for pattern in resolver.url_patterns:
        try:
            from django.urls import URLResolver, URLPattern
            if isinstance(pattern, URLResolver):
                sub_prefix = prefix + str(pattern.pattern)
                endpoints.extend(_collect_endpoints(pattern, sub_prefix))
            elif isinstance(pattern, URLPattern):
                path = prefix + str(pattern.pattern)
                if not path or path.startswith("__"):
                    continue
                callback = pattern.callback
                versions = getattr(callback, "_epb_versions", {})
                # Django doesn't encode HTTP methods in URLPattern; use ANY
                endpoints.append({
                    "path": "/" + path.lstrip("/"),
                    "action": "ANY",
                    "versions": versions,
                })
        except Exception:
            continue
    return endpoints
