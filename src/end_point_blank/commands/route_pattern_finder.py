from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RoutePatternFinder:
    """
    Extracts the matched route pattern from a WSGI request environ.

    Supports Flask (via ``url_rule``) and Django (via ``resolver_match``).
    Returns ``None`` when the pattern cannot be determined (e.g. 404s).

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::RoutePatternFinder``.
    """

    @staticmethod
    def find(environ: Dict[str, Any]) -> Optional[str]:
        try:
            # Flask stores the matched URL rule in the environ under
            # 'werkzeug.request' or as a 'url_rule' attribute injected by the
            # routing machinery.
            url_rule = environ.get("werkzeug.request")
            if url_rule is not None and hasattr(url_rule, "url_rule"):
                rule = url_rule.url_rule
                if rule is not None:
                    return str(rule)

            # Django attaches a resolver_match object to the request object
            # stored in the environ.
            django_request = environ.get("django.request")
            if django_request is not None:
                resolver_match = getattr(django_request, "resolver_match", None)
                if resolver_match is not None:
                    return resolver_match.route or None

        except Exception as exc:  # pragma: no cover
            logger.debug("RoutePatternFinder: %s", exc)

        return None
