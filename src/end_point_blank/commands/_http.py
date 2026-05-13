"""Internal HTTP helper shared across command classes."""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_local = threading.local()
_TIMEOUT = 15  # seconds


def _session() -> requests.Session:
    """Return a per-thread requests.Session (Session is not thread-safe)."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def _new_session() -> requests.Session:
    """Replace the current thread's session with a fresh one."""
    _local.session = requests.Session()
    return _local.session


def post(url: str, auth_header: str, body: Dict[str, Any]) -> Optional[requests.Response]:
    """
    POST *body* as JSON to *url* with *auth_header*.

    On ConnectionError the session is recreated (to flush stale keep-alive
    connections) and the request is retried once before giving up.

    :returns: The :class:`requests.Response`, or ``None`` on network error.
    """
    for attempt in range(2):
        try:
            return _session().post(
                url,
                json=body,
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
        except requests.exceptions.ConnectionError as exc:
            if attempt == 0:
                logger.warning("HTTP POST to %s failed, retrying with fresh connection: %s", url, exc)
                _new_session()
            else:
                logger.error("HTTP POST to %s failed: %s", url, exc)
        except requests.RequestException as exc:
            logger.error("HTTP POST to %s failed: %s", url, exc)
            return None

    return None
