from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..configuration import Configuration, LogMode
from ..request_store import RequestStore
from .direct_writer import DirectWriter
from .delayed_writer import DelayedWriter

logger = logging.getLogger(__name__)


class LogWriter:
    """
    Sends structured log entries to the EndPointBlank API.

    Equivalent to the Ruby gem's ``EndPointBlank::Writers::LogWriter``.

    Usage::

        from end_point_blank.writers.log_writer import LogWriter

        LogWriter.info("Payment processed", {"amount": 42})
        LogWriter.error("Database timeout")
    """

    @staticmethod
    def info(message: str, data: Optional[Dict[str, Any]] = None) -> None:
        LogWriter.write(message, "info", data)

    @staticmethod
    def warn(message: str, data: Optional[Dict[str, Any]] = None) -> None:
        LogWriter.write(message, "warn", data)

    @staticmethod
    def error(message: str, data: Optional[Dict[str, Any]] = None) -> None:
        LogWriter.write(message, "error", data)

    @staticmethod
    def fatal(message: str, data: Optional[Dict[str, Any]] = None) -> None:
        LogWriter.write(message, "fatal", data)

    @staticmethod
    def write(
        message: str,
        level: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send a log entry to the EndPointBlank API."""
        try:
            config = Configuration()
            environ = RequestStore.get()
            uuid = environ.get("HTTP_X_REQUEST_ID") if environ else None
            payload = {
                "message": message,
                "log_level": level,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "app_name": config.app_name,
                "uuid": uuid,
                "data": data or {},
                "source_application_environment_id": (
                    RequestStore.get_source_application_environment_id()
                ),
            }
            _writer().write([payload])
        except Exception as exc:
            logger.error("LogWriter failed: %s", exc)


def _writer():
    config = Configuration()
    return (
        DelayedWriter("log_url")
        if config.log_mode == LogMode.DELAYED
        else DirectWriter("log_url")
    )
