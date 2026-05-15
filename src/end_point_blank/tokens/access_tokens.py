from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_REFRESH_BUFFER = timedelta(minutes=2)
_MIN_TTL = timedelta(seconds=30)


class AccessTokens:
    """
    Thread-safe singleton cache for access tokens keyed by hostname.

    Tokens are automatically refreshed when they are within 2 minutes of expiry.
    Equivalent to the Ruby gem's ``EndPointBlank::AccessTokens``.
    """

    _instance: Optional["AccessTokens"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "AccessTokens":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._tokens: Dict[str, dict] = {}
                    instance._locks: Dict[str, threading.Lock] = {}
                    instance._meta_lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    def _lock_for(self, hostname: str) -> threading.Lock:
        with self._meta_lock:
            if hostname not in self._locks:
                self._locks[hostname] = threading.Lock()
            return self._locks[hostname]

    def _evict_expired(self) -> None:
        now = datetime.now(tz=timezone.utc)
        with self._meta_lock:
            expired = [k for k, e in self._tokens.items() if e["expired_at"] <= now]
            for k in expired:
                self._tokens.pop(k, None)
                self._locks.pop(k, None)

    def token(self, hostname: str) -> Optional[str]:
        """
        Returns a valid access token for *hostname*, fetching a new one if needed.

        :param hostname: The hostname to retrieve a token for.
        :returns: The access token string, or ``None`` if generation failed.
        """
        key = hostname.lower()
        lock = self._lock_for(key)

        with lock:
            entry = self._tokens.get(key)
            if entry and entry["expired_at"] > datetime.now(tz=timezone.utc) + _REFRESH_BUFFER:
                return entry["token"]

            from ..commands.generate_access_token import GenerateAccessToken
            payload = GenerateAccessToken.token(hostname)

            if payload and payload.get("token"):
                self._tokens[key] = {
                    "token": payload["token"],
                    "expired_at": self._parse_expiry(payload.get("expired_at")),
                }
                self._evict_expired()
                return payload["token"]
            else:
                self._tokens.pop(key, None)
                error = payload.get("error") if payload else "unknown error"
                logger.error("Failed to generate access token for %s: %s", hostname, error)
                return None

    def exists(self, hostname: str) -> bool:
        """Returns ``True`` if a token with at least 30 seconds remaining exists for *hostname*."""
        key = hostname.lower()
        entry = self._tokens.get(key)
        return bool(entry and entry["expired_at"] > datetime.now(tz=timezone.utc) + _MIN_TTL)

    def remove(self, hostname: str) -> None:
        """Removes the cached token for *hostname*."""
        self._tokens.pop(hostname.lower(), None)

    def clear(self) -> None:
        """Clears all cached tokens."""
        self._tokens.clear()

    @staticmethod
    def _parse_expiry(value) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(tz=timezone.utc) + timedelta(hours=1)
