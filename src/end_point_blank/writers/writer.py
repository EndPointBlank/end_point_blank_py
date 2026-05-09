from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..configuration import Configuration, LogMode
from ..payload_builder import PayloadBuilder
from .delayed_writer import DelayedWriter
from .direct_writer import DirectWriter


class Writer:
    """
    Factory writer that delegates to :class:`DirectWriter` or :class:`DelayedWriter`
    based on the configured :attr:`~end_point_blank.configuration.Configuration.log_mode`.

    Equivalent to the Ruby gem's ``EndPointBlank::Writers::Writer``.
    """

    def __init__(self, url_key: str) -> None:
        self._url_key = url_key
        self._writer = None

    def write(
        self,
        *,
        message: str,
        exc: Optional[BaseException] = None,
        status: int,
        headers: Optional[Dict[str, str]] = None,
        path: Optional[str] = None,
        action: Optional[str] = None,
        version: Optional[str] = None,
        sent_at: Optional[datetime] = None,
    ) -> None:
        """Builds a payload from the given details and sends it via the appropriate writer."""
        payload = PayloadBuilder.build(
            message=message,
            exc=exc,
            status=status,
            headers=headers or {},
            sent_at=sent_at,
            path=path,
            action=action,
            version=version,
        )
        self._get_writer().write([payload])

    def _get_writer(self):
        if self._writer is None:
            mode = Configuration().log_mode
            self._writer = (
                DelayedWriter(self._url_key)
                if mode == LogMode.DELAYED
                else DirectWriter(self._url_key)
            )
        return self._writer
