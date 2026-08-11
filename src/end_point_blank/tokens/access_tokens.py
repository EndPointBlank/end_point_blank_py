from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_REFRESH_BUFFER = timedelta(minutes=2)
_MIN_TTL = timedelta(seconds=30)


class AccessTokens:
    """
    Thread-safe singleton holding this process's access token.

    Intake issues a token against the application environment the
    authenticating credential belongs to. The hostname sent with a generation
    request only resolves the target server-side; it is not what the token is
    scoped to. A process authenticates as exactly one application environment,
    so it holds exactly one token, whatever hostnames its callers address it by.

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
                    instance._entry: Optional[dict] = None
                    instance._lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    def token(self, hostname: str) -> Optional[str]:
        """
        Returns a valid access token, fetching a new one if none is held or the
        held one is close to expiry.

        :param hostname: The hostname to send with a generation request. It
            tells intake which application environment to resolve and does not
            select which held token comes back — every caller shares one.
        :returns: The access token string, or ``None`` if generation failed.
        """
        entry = self._entry
        if self._usable(entry):
            return entry["token"]

        with self._lock:
            # Another caller may have replaced it while this one waited.
            entry = self._entry
            if self._usable(entry):
                return entry["token"]

            from ..commands.generate_access_token import GenerateAccessToken
            payload = GenerateAccessToken.token(hostname)

            if payload and payload.get("token"):
                self._entry = {
                    "token": payload["token"],
                    "expired_at": self._parse_expiry(payload.get("expired_at")),
                }
                return payload["token"]
            else:
                # A failed refresh must not leave the expiring token behind
                # claiming to be usable — callers would keep presenting it
                # right up to the 401.
                self._entry = None
                error = payload.get("error") if payload else "unknown error"
                logger.error("Failed to generate access token for %s: %s", hostname, error)
                return None

    def exists(self) -> bool:
        """Returns ``True`` if a token with at least 30 seconds remaining is held."""
        entry = self._entry
        return bool(entry and entry["expired_at"] > datetime.now(tz=timezone.utc) + _MIN_TTL)

    def invalidate(self, stale_token: Optional[str]) -> None:
        """
        Discards the held token, but only if it is still the one the caller had.

        Every request in flight when a token is rejected reports the same stale
        value. Only the first of them should cause an exchange — the rest are
        holding a token that has already been replaced, and clearing on their
        behalf would discard a good token and stampede intake.

        :param stale_token: The token the caller was rejected for; ignored when
            it is not the one currently held.
        """
        if stale_token is None:
            return

        with self._lock:
            if self._entry and self._entry["token"] == stale_token:
                self._entry = None

    def clear(self) -> None:
        """Discards the held token."""
        with self._lock:
            self._entry = None

    @staticmethod
    def _usable(entry: Optional[dict]) -> bool:
        return bool(entry and entry["expired_at"] > datetime.now(tz=timezone.utc) + _REFRESH_BUFFER)

    @staticmethod
    def _parse_expiry(value) -> datetime:
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(tz=timezone.utc) + timedelta(hours=1)
