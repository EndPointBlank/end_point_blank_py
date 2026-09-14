from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from ..configuration import Configuration


_MAX_SIZE = 1000


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
    - A ``cache_ttl`` of ``<= 0`` disables the cache. Whenever a read
      (``retrieve``/``exists``) or a ``store()`` observes the cache
      disabled, it clears the ENTIRE cache -- every entry, not just the one
      looked up or written -- under the same lock it makes that observation
      in (see :meth:`_current_ttl_or_clear_locked`). This matches the
      Elixir SDK's sc-660 ``AuthCache.clear/0``.

      Known, documented gap: a disable followed by a re-enable with **no**
      cache read or store in between flushes nothing, because nothing ever
      observes the disabled state to trigger the clear. This is deliberate
      -- there is no configure-time flushing in this story.
    - This instance, like the process it lives in, is not shared: under a
      multi-worker server (gunicorn, uWSGI, ...) each worker holds its own
      singleton and its own cache. A disabled read or store in one worker
      clears only that worker's cache; every worker must itself observe a
      disabled read or store before its own cache is cleared.

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

    def _current_ttl_or_clear_locked(self) -> Optional[int]:
        """
        Must be called while holding ``self._lock``. Reads the currently
        configured ``cache_ttl``. If it means "disabled", clears the ENTIRE
        cache right here -- in the same critical section as the read -- and
        returns ``None``; otherwise returns the ttl unchanged.

        Centralizing this in one locked helper, called from ``retrieve``,
        ``exists`` and ``store`` alike, is what makes "whenever a read or a
        store observes the cache disabled" true for every call site: none
        of them can read ``cache_ttl`` before taking the lock, so a store
        racing a disabling read (or the reverse) cannot land a stale entry
        after the clear -- the check and the clear-or-proceed are one
        critical section.
        """
        cache_ttl = Configuration().cache_ttl
        if _disabled(cache_ttl):
            self._cache.clear()
            return None
        return cache_ttl

    @staticmethod
    def _is_valid(entry: dict, now: datetime, cache_ttl: int) -> bool:
        """
        Whether *entry* is still a hit, given the *currently configured*
        ``cache_ttl`` (already confirmed enabled by the caller). Both
        conditions must hold:

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
        if now >= entry["expires_at"]:
            return False
        return (now - entry["written_at"]) < timedelta(seconds=cache_ttl)

    def store(self, key: str, credentials: Any) -> None:
        """
        Stores *credentials* under *key* if non-``None`` and the cache is
        currently enabled.

        A store that observes ``cache_ttl`` disabled (``<= 0``) clears the
        entire cache and inserts nothing -- see
        :meth:`_current_ttl_or_clear_locked`.

        :param key: The cache key.
        :param credentials: The credentials to cache.
        """
        if credentials is None:
            return
        with self._lock:
            cache_ttl = self._current_ttl_or_clear_locked()
            if cache_ttl is None:
                return
            now = datetime.now(tz=timezone.utc)
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

        A read that observes ``cache_ttl`` disabled clears the entire
        cache, not just *key* -- see :meth:`_current_ttl_or_clear_locked`.
        Otherwise, an entry present but no longer valid is deleted here,
        not merely hidden. Every observation-and-mutation happens under
        ``self._lock``, so a concurrent ``store()`` cannot race a delete or
        a clear for the same key: one completes fully before the other
        starts.

        :returns: The cached credentials, or ``None`` if absent or invalid.
        """
        with self._lock:
            cache_ttl = self._current_ttl_or_clear_locked()
            if cache_ttl is None:
                return None
            entry = self._cache.get(key)
            if entry is None:
                return None
            now = datetime.now(tz=timezone.utc)
            if self._is_valid(entry, now, cache_ttl):
                return entry["credentials"]
            del self._cache[key]
            return None

    def exists(self, key: str) -> bool:
        """Returns ``True`` if a valid entry exists for *key* under the
        currently configured ``cache_ttl`` (see :meth:`retrieve`). A read
        that observes the cache disabled clears the entire cache, the same
        as :meth:`retrieve`."""
        with self._lock:
            cache_ttl = self._current_ttl_or_clear_locked()
            if cache_ttl is None:
                return False
            entry = self._cache.get(key)
            if entry is None:
                return False
            now = datetime.now(tz=timezone.utc)
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
