import datetime as dt
import time
import pytest
from end_point_blank.commands import authentication_cache as authentication_cache_module
from end_point_blank.commands.authentication_cache import AuthenticationCache
from end_point_blank.configuration import Configuration


def _freeze(monkeypatch, when):
    """Pin the cache's clock (module-level ``datetime.now(tz=...)``) to a
    fixed instant, so tests can move time forward deterministically instead
    of sleeping in real time. Patches the ``datetime`` name inside the
    authentication_cache module -- the same wall clock the cache already
    uses, just with a controlled value -- so it works whether or not the
    module has any TTL-on-read logic at all (i.e. against unmodified
    origin/master too).
    """

    class _Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    monkeypatch.setattr(authentication_cache_module, "datetime", _Frozen)


@pytest.fixture(autouse=True)
def reset():
    cache = AuthenticationCache()
    cache.clear()
    Configuration().cache_ttl = 300
    yield
    cache.clear()


def test_singleton():
    assert AuthenticationCache() is AuthenticationCache()


def test_store_and_retrieve():
    cache = AuthenticationCache()
    cache.store("key1", "credentials1")
    assert cache.retrieve("key1") == "credentials1"


def test_retrieve_returns_none_for_missing_key():
    assert AuthenticationCache().retrieve("nonexistent") is None


def test_exists_true_for_stored_key():
    cache = AuthenticationCache()
    cache.store("key1", "creds")
    assert cache.exists("key1") is True


def test_exists_false_for_missing_key():
    assert AuthenticationCache().exists("missing") is False


def test_remove_deletes_entry():
    cache = AuthenticationCache()
    cache.store("key1", "creds")
    removed = cache.remove("key1")
    assert removed == "creds"
    assert cache.retrieve("key1") is None


def test_clear_removes_all():
    cache = AuthenticationCache()
    cache.store("key1", "creds1")
    cache.store("key2", "creds2")
    cache.clear()
    assert cache.size() == 0


def test_size_reflects_count():
    cache = AuthenticationCache()
    cache.store("key1", "creds1")
    cache.store("key2", "creds2")
    assert cache.size() == 2


def test_keys_returns_all():
    cache = AuthenticationCache()
    cache.store("alpha", "creds")
    cache.store("beta", "creds")
    assert "alpha" in cache.keys()
    assert "beta" in cache.keys()


def test_store_ignores_none():
    cache = AuthenticationCache()
    cache.store("key1", None)
    assert cache.retrieve("key1") is None
    assert not cache.exists("key1")


def test_expired_entry_returns_none():
    Configuration().cache_ttl = 0
    cache = AuthenticationCache()
    cache.store("key1", "creds")
    time.sleep(0.01)
    assert cache.retrieve("key1") is None


class TestEviction:
    """The cache is process-lifetime and keyed on client credentials + route, so
    without eviction a busy service accumulates an entry per distinct caller
    forever. Both bounds below are what keep it from becoming a memory leak."""

    def test_expired_entries_are_evicted_when_something_new_is_stored(self):
        cache = AuthenticationCache()
        Configuration().cache_ttl = 0
        cache.store("stale", "creds")
        time.sleep(0.01)

        Configuration().cache_ttl = 300
        cache.store("fresh", "creds")

        assert cache.keys() == ["fresh"]

    def test_the_cache_is_capped(self):
        cache = AuthenticationCache()

        for n in range(1005):
            cache.store(f"key{n}", "creds")

        assert cache.size() == 1000

    def test_the_oldest_entry_is_dropped_when_the_cap_is_reached(self):
        cache = AuthenticationCache()

        for n in range(1001):
            cache.store(f"key{n}", "creds")

        assert cache.retrieve("key0") is None
        assert cache.retrieve("key1000") == "creds"


class TestRuntimeCacheTtlChanges:
    """sc-755: cache_ttl is re-evaluated on EVERY read against the TTL
    *currently* configured, anchored to each entry's write time -- not only
    against the TTL that was in effect when the entry was written.
    """

    def test_a_lowering_ttl_expires_an_already_cached_entry_on_its_next_read(self, monkeypatch):
        cache = AuthenticationCache()
        t0 = dt.datetime.now(dt.timezone.utc)
        _freeze(monkeypatch, t0)
        Configuration().cache_ttl = 300
        cache.store("key1", "creds")

        Configuration().cache_ttl = 10
        _freeze(monkeypatch, t0 + dt.timedelta(seconds=11))

        assert cache.retrieve("key1") is None

    def test_b_lowered_ttl_is_not_clamped_against_remaining_time_to_original_expiry(self, monkeypatch):
        # ttl 300 -> store at t0 -> ttl 10 -> read at t0+295. Remaining time to
        # the *original* expiry (t0+300) is only 5s, which is < the new ttl of
        # 10s. A naive "expires_at - now <= current_ttl" clamp would call this
        # a HIT (5 <= 10) even though 295s -- far more than the new 10s window
        # -- have elapsed since write. Must be a MISS.
        cache = AuthenticationCache()
        t0 = dt.datetime.now(dt.timezone.utc)
        _freeze(monkeypatch, t0)
        Configuration().cache_ttl = 300
        cache.store("key1", "creds")

        Configuration().cache_ttl = 10
        _freeze(monkeypatch, t0 + dt.timedelta(seconds=295))

        assert cache.retrieve("key1") is None

    def test_c_raising_ttl_never_extends_an_entry_past_its_original_expiry(self, monkeypatch):
        cache = AuthenticationCache()
        t0 = dt.datetime.now(dt.timezone.utc)
        _freeze(monkeypatch, t0)
        Configuration().cache_ttl = 10
        cache.store("key1", "creds")

        Configuration().cache_ttl = 300
        _freeze(monkeypatch, t0 + dt.timedelta(seconds=11))

        assert cache.retrieve("key1") is None

    def test_d_disabling_actually_removes_the_entry_and_re_enabling_does_not_resurrect_it(
        self, monkeypatch
    ):
        cache = AuthenticationCache()
        t0 = dt.datetime.now(dt.timezone.utc)
        _freeze(monkeypatch, t0)
        Configuration().cache_ttl = 300
        cache.store("key1", "creds")
        assert cache.size() == 1

        Configuration().cache_ttl = 0
        assert cache.retrieve("key1") is None
        assert cache.size() == 0  # actually deleted, not merely hidden

        Configuration().cache_ttl = 300
        assert cache.retrieve("key1") is None  # not resurrected
        assert cache.size() == 0

    def test_e_unchanged_ttl_within_window_is_still_a_hit(self, monkeypatch):
        cache = AuthenticationCache()
        t0 = dt.datetime.now(dt.timezone.utc)
        _freeze(monkeypatch, t0)
        Configuration().cache_ttl = 300
        cache.store("key1", "creds")

        _freeze(monkeypatch, t0 + dt.timedelta(seconds=5))

        assert cache.retrieve("key1") == "creds"

    def test_store_while_disabled_inserts_nothing(self, monkeypatch):
        cache = AuthenticationCache()
        Configuration().cache_ttl = 0
        cache.store("key1", "creds")
        assert cache.size() == 0
        assert cache.keys() == []

    def test_exists_is_false_and_deletes_when_a_present_entry_is_invalidated_by_a_lowered_ttl(
        self, monkeypatch
    ):
        cache = AuthenticationCache()
        t0 = dt.datetime.now(dt.timezone.utc)
        _freeze(monkeypatch, t0)
        Configuration().cache_ttl = 300
        cache.store("key1", "creds")

        Configuration().cache_ttl = 10
        _freeze(monkeypatch, t0 + dt.timedelta(seconds=11))

        assert cache.exists("key1") is False
        assert cache.size() == 0  # actually deleted, not merely hidden

    def test_store_sweeps_a_sibling_entry_invalidated_by_a_lowered_ttl(self, monkeypatch):
        cache = AuthenticationCache()
        t0 = dt.datetime.now(dt.timezone.utc)
        _freeze(monkeypatch, t0)
        Configuration().cache_ttl = 300
        cache.store("stale", "creds")

        Configuration().cache_ttl = 10
        _freeze(monkeypatch, t0 + dt.timedelta(seconds=11))
        cache.store("fresh", "creds")

        assert cache.keys() == ["fresh"]
