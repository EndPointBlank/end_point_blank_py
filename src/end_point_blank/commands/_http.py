"""Internal HTTP helper shared across command classes."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_local = threading.local()
_TIMEOUT = 15  # seconds
_RETRY_DELAYS = [0.5, 1.5, 3.0]  # seconds between attempts 1→2, 2→3, 3→4


def _session() -> requests.Session:
    """Return a per-thread requests.Session (Session is not thread-safe)."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def post(url: str, auth_header: str, body: Dict[str, Any]) -> Optional[requests.Response]:
    """
    POST *body* as JSON to *url* with *auth_header*.

    Retries up to 3 times with exponential backoff on connection errors.

    :returns: The :class:`requests.Response`, or ``None`` on network error.
    """
    last_exc: Optional[Exception] = None
    for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return _session().post(
                url,
                json=body,
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
        except requests.exceptions.ConnectionError as exc:
            last_exc = exc
            logger.warning("HTTP POST to %s failed (attempt %d/4): %s", url, attempt + 1, exc)
        except requests.RequestException as exc:
            logger.error("HTTP POST to %s failed: %s", url, exc)
            return None

    logger.error("HTTP POST to %s failed after 4 attempts: %s", url, last_exc)
    return None
