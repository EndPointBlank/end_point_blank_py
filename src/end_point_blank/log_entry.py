from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class LogEntry:
    """
    Represents a log entry capturing details about an error or interaction.

    Equivalent to the Ruby gem's ``EndPointBlank::LogEntry``.
    """

    def __init__(
        self,
        *,
        message: str,
        stacktrace: Optional[List[str]],
        app: Optional[str],
        status: int,
        headers: Dict[str, str],
        body: Optional[str],
        env: Optional[Dict[str, Any]],
        sent_at: Optional[datetime] = None,
    ) -> None:
        self.message = message
        self.stacktrace = stacktrace
        self.app = app
        self.status = status
        self.headers = headers
        self.body = body
        self.env = env
        self.sent_at = sent_at or datetime.utcnow()
