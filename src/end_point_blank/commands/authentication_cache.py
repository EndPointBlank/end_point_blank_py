from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from ..configuration import Configuration


_MAX_SIZE = 1000


def _now() -> datetime:
    """The cache's clock: wall-clock UTC. A thin, mockable indirection over
    ``datetime.now(tz=timezone.utc)`` -- the same clock, not a different
    one -- so tests can control elapsed time deterministically instead of
    sleeping in real time.
    """
    return datetime.now(tz=timezone.utc)


def _disabled(cache_ttl: int) -> bool:
    """``cache_ttl <= 0`` means the cache is off."""
    return cache_ttl <= 0


class AuthenticationCache:
    """
    Thread-safe singleton cache for storing authentication credentials.

    Capped at ``_MAX_SIZE`` entries. When full, entries no longer valid
    under the currently configured TTL are evicted first; if still at
    capacity the oldest entry (by insertion order) is removed.

    Each entry records its write time and its own expiry (computed from the
    TTL in effect when it was written). On every read, validity is
    re-derived against the TTL configured *now* -- see :meth:`_is_valid` --
    not only against the TTL that was in effect at write time. Concretely:

    - Lowering :attr:`~end_point_blank.configuration.Configuration.cache_ttl`
      at runtime shortens the remaining life of entries already cached,
      taking effect on their next read.
    - Raising it never extends an entry past the expiry it was written
      with.
    - A ``cache_ttl`` of ``<= 0`` disables the cache: any entry found on
      read is treated as a miss and deleted (not merely hidden), and a
      ``store()`` performed while disabled inserts nothing.

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

    @staticmethod
    def _is_valid(entry: dict, now: datetime, cache_ttl: int) -> bool:
        """
        Whether *entry* is still a hit, given the *currently configured*
        ``cache_ttl``. Both conditions must hold:

        - ``now < expires_at``: the entry's own expiry -- fixed at write
          time from the TTL then in effect -- has not passed. Raising the
          TTL afterwards never extends this.
        - ``now - written_at < cache_ttl``: the entry has not outlived the
          TTL configured *now*. Lowering the TTL applies retroactively to
          entries already cached, starting from their next read.

        Deliberately NOT ``expires_at - now <= cache_ttl``: that
        remaining-time clamp is wrong. As real time passes, an old entry's
        time-remaining-to-its-original-expiry can coincidentally fall back
        under a new, shorter TTL window and look valid again even though it
        is older than the new TTL allows.
        """
        if _disabled(cache_ttl):
            return False
        if now >= entry["expires_at"]:
            return False
        return (now - entry["written_at"]) < timedelta(seconds=cache_ttl)

    def store(self, key: str, credentials: Any) -> None:
        """
        Stores *credentials* under *key* if non-``None`` and the cache is
        currently enabled. A store performed while ``cache_ttl`` is
        disabled (``<= 0``) inserts nothing.

        :param key: The cache key.
        :param credentials: The credentials to cache.
        """
        if credentials is None:
            return
        cache_ttl = Configuration().cache_ttl
        if _disabled(cache_ttl):
            return
        now = _now()
        with self._lock:
            # Evict entries no longer valid under the currently configured
            # TTL first.
            stale = [k for k, e in self._cache.items() if not self._is_valid(e, now, cache_ttl)]
            for k in stale:
                del self._cache[k]
            # Evict oldest insertion(s) if still at capacity
            while len(self._cache) >= _MAX_SIZE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = {
                "credentials": credentials,
                "written_at": now,
                "expires_at": now + timedelta(seconds=cache_ttl),
            }

    def retrieve(self, key: str) -> Optional[Any]:
        """
        Returns credentials for *key* if a valid entry exists under the
        currently configured ``cache_ttl`` (see :meth:`_is_valid`).

        An entry that is no longer valid -- including one found while the
        cache is disabled -- is deleted here, not merely hidden. The
        read-then-delete is atomic under ``self._lock``, so a concurrent
        ``store()`` for the same key cannot race with the delete: it either
        completes fully before or fully after this read.

        :returns: The cached credentials, or ``None`` if absent or invalid.
        """
        cache_ttl = Configuration().cache_ttl
        now = _now()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if self._is_valid(entry, now, cache_ttl):
                return entry["credentials"]
            del self._cache[key]
            return None

    def exists(self, key: str) -> bool:
        """Returns ``True`` if a valid entry exists for *key* under the
        currently configured ``cache_ttl`` (see :meth:`retrieve`)."""
        cache_ttl = Configuration().cache_ttl
        now = _now()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if self._is_valid(entry, now, cache_ttl):
                return True
            del self._cache[key]
            return False

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
