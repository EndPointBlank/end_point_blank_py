from __future__ import annotations

import logging
import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..configuration import Configuration, LogMode
from ..request_store import RequestStore
from .direct_writer import DirectWriter
from .delayed_writer import DelayedWriter

logger = logging.getLogger(__name__)

# uuid is taken from RequestStore.get_uuid() — same source as RequestWriter,
# ResponseWriter, and ExceptionWriter — so all four rows correlate on the same
# request. This used to read environ["HTTP_X_REQUEST_ID"] directly, which
# carried the caller's own inbound id instead of the SDK's own uuid, so log
# rows never joined to the other three streams for one interaction. That is a
# deliberate trade-off, not an oversight: a customer's own inbound trace id no
# longer appears on log rows. It never appeared on the other three streams
# either, so this removes the one outlier rather than making log rows less
# capable than they used to be. Preserving the caller's id alongside our own,
# as a second field, is real future work and is out of scope here. See sc-380.
#
# Outside a request there's nothing in RequestStore and get_uuid() returns
# None; mint one instead, the same reasoning ExceptionWriter uses under
# sc-378 — a log call outside a request (background job, startup) is exactly
# as possible as an exception one, and application_logs has no required
# fields to lean on to protect against a None uuid anyway.


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
            uuid = RequestStore.get_uuid() or str(_uuid_mod.uuid4())
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
            # The log record type has nothing maskable (FIELD_MAP[log] is empty);
            # the log writer only stamps the endpoint fields, parity with js.
            env = environ or {}
            payload["stamped_path"] = env.get("PATH_INFO")
            payload["stamped_http_method"] = env.get("REQUEST_METHOD")
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
