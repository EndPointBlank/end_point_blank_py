from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

from ..configuration import Configuration, LogMode
from ..request_store import RequestStore
from .direct_writer import DirectWriter
from .delayed_writer import DelayedWriter

logger = logging.getLogger(__name__)


class ExceptionWriter:
    """
    Sends unhandled application exception payloads to the EndPointBlank API.

    Equivalent to the Ruby gem's ``EndPointBlank::Writers::ExceptionWriter``.

    Usage::

        from end_point_blank.writers.exception_writer import ExceptionWriter

        try:
            risky_call()
        except Exception as exc:
            ExceptionWriter.write(exc)
            raise
    """

    @staticmethod
    def write(exc: BaseException) -> None:
        """Send *exc* details to the EndPointBlank API (fire-and-forget)."""
        try:
            config = Configuration()
            environ = RequestStore.get()
            uuid = _extract_uuid(environ)
            payload = {
                "app_name": config.app_name,
                "message": str(exc),
                "stacktrace": traceback.format_exc(),
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "source_application_environment_id": (
                    RequestStore.get_source_application_environment_id()
                ),
                "uuid": uuid,
            }
            _writer().write([payload])
        except Exception as reporting_exc:
            logger.error("ExceptionWriter failed: %s", reporting_exc)


def _writer():
    config = Configuration()
    return (
        DelayedWriter("application_errors_url")
        if config.log_mode == LogMode.DELAYED
        else DirectWriter("application_errors_url")
    )


def _extract_uuid(environ: Optional[dict]) -> Optional[str]:
    if environ is None:
        return None
    return environ.get("HTTP_X_REQUEST_ID")
