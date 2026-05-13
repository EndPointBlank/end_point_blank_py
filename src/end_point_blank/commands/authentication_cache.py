from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from ..configuration import Configuration


class AuthenticationCache:
    """
    Thread-safe singleton cache for storing authentication credentials.

    Entries expire after :attr:`~end_point_blank.configuration.Configuration.cache_ttl`
    seconds (default: 300).

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::AuthenticationCache``.
    """

    _instance: Optional["AuthenticationCache"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "AuthenticationCache":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._cache: Dict[str, dict] = {}
                    instance._lock = threading.Lock()
                    cls._instance = instance
        return cls._instance

    def store(self, key: str, credentials: Any) -> None:
        """
        Stores *credentials* under *key* if non-``None``.

        :param key: The cache key.
        :param credentials: The credentials to cache.
        """
        if credentials is None:
            return
        ttl = Configuration().cache_ttl
        now = datetime.now(tz=timezone.utc)
        with self._lock:
            self._cache[key] = {
                "credentials": credentials,
                "expired_at": now + timedelta(seconds=ttl),
            }
            expired = [k for k, e in self._cache.items() if e["expired_at"] <= now]
            for k in expired:
                del self._cache[k]

    def retrieve(self, key: str) -> Optional[Any]:
        """
        Returns credentials for *key* if they exist and have not expired.

        :returns: The cached credentials, or ``None`` if absent or expired.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry["expired_at"] > datetime.now(tz=timezone.utc):
                return entry["credentials"]
            del self._cache[key]
            return None

    def exists(self, key: str) -> bool:
        """Returns ``True`` if a non-expired entry exists for *key*."""
        with self._lock:
            entry = self._cache.get(key)
            return bool(entry and entry["expired_at"] > datetime.now(tz=timezone.utc))

    def remove(self, key: str) -> Optional[Any]:
        """
        Removes the entry for *key*.

        :returns: The removed credentials, or ``None`` if not present.
        """
        with self._lock:
            entry = self._cache.pop(key, None)
            return entry["credentials"] if entry else None

    def clear(self) -> None:
        """Clears all cached entries."""
        with self._lock:
            self._cache.clear()

    def keys(self) -> List[str]:
        """Returns all current cache keys (including potentially expired ones)."""
        with self._lock:
            return list(self._cache.keys())

    def size(self) -> int:
        """Returns the number of entries currently in the cache."""
        with self._lock:
            return len(self._cache)
