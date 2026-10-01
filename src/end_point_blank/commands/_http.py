"""Internal HTTP helper shared across command classes."""
from __future__ import annotations

import logging
import ssl
import threading
import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter

from .. import VERSION

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 3  # seconds — TCP/TLS handshake budget per attempt
_READ_TIMEOUT = 5  # seconds — time to first response byte per attempt
# 3 retries × (3+5)s + 2 × 200ms ≈ 24.4s worst case, within 30s client timeouts
_local = threading.local()


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


def _session() -> requests.Session:
    """Return a per-thread Session with the tolerant SSL adapter mounted."""
    if not hasattr(_local, "session"):
        s = requests.Session()
        s.mount("https://", _SSLAdapter())
        _local.session = s
    return _local.session


def sdk_header() -> str:
    """
    The ``x-epb-sdk`` value sent on every call to intake: ``python/<version>``,
    this library's ``VERSION`` (sc-1463). intake ignores it today; it is there
    so intake can record the oldest version seen per credential for the move
    gate. That gate's minimum Python version is the release that turns
    ``derive_base_url_from_client_id`` on by default, not the one that added
    this header: with the option at its default, this version keeps calling
    ``in.endpointblank.com`` after its organization moves.
    """
    return f"python/{VERSION}"


def post(url: str, auth_header: str, body: Dict[str, Any]) -> Optional[requests.Response]:
    """
    POST *body* as JSON to *url* with *auth_header*.

    Uses a per-thread persistent session (amortising TLS handshake cost)
    with an SSL adapter that tolerates missing close_notify alerts.

    :returns: The :class:`requests.Response`, or ``None`` on network error.
    """
    for attempt in range(1, 4):
        try:
            return _session().post(
                url,
                json=body,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "x-epb-sdk": sdk_header(),
                },
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            logger.error("HTTP POST to %s failed (attempt %d/3): %s", url, attempt, exc)
            if attempt < 3:
                time.sleep(0.2)
    return None
