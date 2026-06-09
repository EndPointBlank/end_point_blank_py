from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..commands.version_finder import VersionFinder
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
                _delayed_writer = DelayedWriter("requests_url")
    return _delayed_writer


class RequestWriter:
    """
    Sends request payloads to the EndPointBlank API.

    Equivalent to the Ruby gem's ``EndPointBlank::Writers::RequestWriter``.

    Called automatically by
    :class:`~end_point_blank.middleware.ReportInteractionMiddleware`.
    """

    @staticmethod
    def write() -> None:
        """Capture the current request from :class:`RequestStore` and send it."""
        try:
            environ = RequestStore.get()
            if environ is None:
                return
            config = Configuration()
            version = VersionFinder().find(environ)
            headers = _extract_headers(environ)
            payload = {
                "app_name": config.app_name,
                "env": config.environment,
                "uuid": RequestStore.get_uuid(),
                "host": environ.get("HTTP_HOST") or environ.get("SERVER_NAME"),
                "headers": headers,
                "path": environ.get("PATH_INFO", ""),
                "http_method": environ.get("REQUEST_METHOD", ""),
                "endpoint_version": version,
                "request": _read_body(environ),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            from ..masking import apply as _mask
            payload = _mask(payload, "request", config.masking_rules, config.mask_hook)
            _writer().write([payload])
        except Exception as exc:
            logger.error("RequestWriter failed: %s", exc)


def _writer():
    config = Configuration()
    return (
        _get_delayed_writer()
        if config.log_mode == LogMode.DELAYED
        else DirectWriter("requests_url")
    )


def _extract_headers(environ: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            headers[name] = value
        elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            name = key.replace("_", "-").title()
            headers[name] = value
    return headers


def _read_body(environ: Dict[str, Any]) -> Optional[str]:
    try:
        import io
        import time
        wsgi_input = environ.get("wsgi.input")
        if wsgi_input is None:
            return None
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
        t0 = time.monotonic()
        logger.debug("[request_writer] reading wsgi.input (type=%s, content_length=%d)", type(wsgi_input).__name__, content_length)
        body = wsgi_input.read(content_length) if content_length > 0 else b""
        logger.debug("[request_writer] wsgi.input.read() done in %.3fs, %d bytes", time.monotonic() - t0, len(body))
        # Replace the consumed stream with a BytesIO so downstream
        # apps (Flask, Django) can still read the body.
        environ["wsgi.input"] = io.BytesIO(body)
        return body.decode("utf-8", errors="replace") if body else None
    except Exception as exc:
        logger.error("[request_writer] _read_body failed: %s", exc)
        return None
