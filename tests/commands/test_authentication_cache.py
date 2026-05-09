import time
import pytest
from end_point_blank.commands.authentication_cache import AuthenticationCache
from end_point_blank.configuration import Configuration


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
