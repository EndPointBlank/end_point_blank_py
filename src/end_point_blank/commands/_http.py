"""Internal HTTP helper shared across command classes."""
from __future__ import annotations

import logging
import ssl
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

_TIMEOUT = 15  # seconds


class _SSLAdapter(HTTPAdapter):
    """
    HTTPAdapter that sets OP_IGNORE_UNEXPECTED_EOF on the SSL context.

    Python 3.12+ is strict about TLS close_notify; many servers (including
    render.com's load balancer) close connections without sending it, which
    raises SSLEOFError. This option restores the pre-3.12 lenient behaviour.
    """

    _ctx: Optional[ssl.SSLContext] = None

    @classmethod
    def _ssl_context(cls) -> ssl.SSLContext:
        if cls._ctx is None:
            ctx = ssl.create_default_context()
            ctx.options |= getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
            cls._ctx = ctx
        return cls._ctx

    def init_poolmanager(self, *args, **kwargs):
        kwargs.setdefault("ssl_context", self._ssl_context())
        super().init_poolmanager(*args, **kwargs)


def post(url: str, auth_header: str, body: Dict[str, Any]) -> Optional[requests.Response]:
    """
    POST *body* as JSON to *url* with *auth_header*.

    Uses a fresh connection per call with a tolerant SSL context.

    :returns: The :class:`requests.Response`, or ``None`` on network error.
    """
    try:
        with requests.Session() as session:
            session.mount("https://", _SSLAdapter())
            return session.post(
                url,
                json=body,
                headers={"Authorization": auth_header, "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
    except requests.RequestException as exc:
        logger.error("HTTP POST to %s failed: %s", url, exc)
        return None
