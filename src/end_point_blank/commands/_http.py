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
from ..configuration_error import ConfigurationError

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


# What a request that never completed raises: a refused or dropped connection,
# DNS, TLS and proxy failures (all ``ConnectionError``), a connect or read
# timeout, and a body cut off mid-stream. Only these are retried and answered
# with None. Every other ``RequestException`` -- ``MissingSchema``,
# ``InvalidURL``, ``InvalidHeader``, a body that will not serialize -- is a
# configuration or programming error that no retry can fix, and calling it a
# network failure would send the reader off to check the network: it
# propagates (sc-1469). Matches the Ruby gem, whose ``Http.post`` rescues only
# ``Excon::Error``.
TRANSPORT_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


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

    :returns: The :class:`requests.Response`, or ``None`` when the request
        never completed (one of :data:`TRANSPORT_ERRORS`, three times).
    :raises requests.RequestException: any other request error, unretried.
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
        except TRANSPORT_ERRORS as exc:
            logger.error("HTTP POST to %s failed (attempt %d/3): %s", url, attempt, exc)
            if attempt < 3:
                time.sleep(0.2)
    return None


def log_unsent(what: str, exc: Exception) -> None:
    """
    Logs an intake call on a request, boot or background path that raised
    instead of answering, so the caller can treat it as unanswered.

    Since sc-1469 a missing credential raises :class:`ConfigurationError` and
    :func:`post` lets a non-transport error through. Neither may crash the
    host application from authenticate/authorize, endpoint registration or the
    writers, which used to answer None or log for every failure. A ``ConfigurationError``'s
    text is the SDK's own and says what to set; anything else is named by
    class only, because requests puts the offending header value -- here, the
    credential -- in an ``InvalidHeader``'s message.
    """
    if isinstance(exc, ConfigurationError):
        logger.error("%s not sent to EndPointBlank: %s", what, exc)
    else:
        logger.error("%s to EndPointBlank failed unexpectedly (%s)", what, type(exc).__name__)
