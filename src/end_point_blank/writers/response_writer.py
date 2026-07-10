from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..commands.route_pattern_finder import RoutePatternFinder
from ..configuration import Configuration, LogMode
from ..request_store import RequestStore
from .direct_writer import DirectWriter
from .delayed_writer import DelayedWriter

logger = logging.getLogger(__name__)

_delayed_writer: Optional[DelayedWriter] = None
_delayed_writer_lock = threading.Lock()


def _get_delayed_writer() -> DelayedWriter:
    global _delayed_writer
    if _delayed_writer is None:
        with _delayed_writer_lock:
            if _delayed_writer is None:
                _delayed_writer = DelayedWriter("responses_url")
    return _delayed_writer


class ResponseWriter:
    """
    Sends response payloads to the EndPointBlank API.

    Equivalent to the Ruby gem's ``EndPointBlank::Writers::ResponseWriter``.

    Called automatically by
    :class:`~end_point_blank.middleware.ReportInteractionMiddleware`.
    """

    @staticmethod
    def write(
        status: Optional[int],
        headers: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send the response details to the EndPointBlank API."""
        try:
            config = Configuration()
            environ = RequestStore.get()
            route = RoutePatternFinder.find(environ) if environ else None
            uuid = RequestStore.get_uuid()
            payload = {
                "app_name": config.app_name,
                "env": config.environment,
                "uuid": uuid,
                "status": status,
                "headers": headers or {},
                "body": _truncate(body),
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "route": route,
                "method": environ.get("REQUEST_METHOD") if environ else None,
                "data": data or {},
                "source_application_environment_id": (
                    RequestStore.get_source_application_environment_id()
                ),
            }
            from ..masking import apply as _mask
            payload = _mask(payload, "response", config.masking_rules, config.mask_hook)
            _writer().write([payload])
        except Exception as exc:
            logger.error("ResponseWriter failed: %s", exc)


def _writer():
    config = Configuration()
    return (
        _get_delayed_writer()
        if config.log_mode == LogMode.DELAYED
        else DirectWriter("responses_url")
    )


def _truncate(body: Optional[str]) -> Optional[str]:
    if body is None:
        return None
    return body[:1024] + "..." if len(body) > 1024 else body
