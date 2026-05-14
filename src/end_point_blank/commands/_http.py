"""Internal HTTP helper shared across command classes."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 15  # seconds


def post(url: str, auth_header: str, body: Dict[str, Any]) -> Optional[requests.Response]:
    """
    POST *body* as JSON to *url* with *auth_header*.

    Retries once on SSL/connection errors — Python 3.12+ raises SSLEOFError
    when a server closes a TLS connection without a proper close_notify alert.
    A second fresh connection to the same server typically succeeds.

    :returns: The :class:`requests.Response`, or ``None`` on network error.
    """
    for attempt in range(2):
        try:
            return requests.post(
                url,
                json=body,
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as exc:
            if attempt == 0:
                logger.warning("HTTP POST to %s failed, retrying: %s", url, exc)
            else:
                logger.error("HTTP POST to %s failed: %s", url, exc)
        except requests.RequestException as exc:
            logger.error("HTTP POST to %s failed: %s", url, exc)
            return None
    return None
