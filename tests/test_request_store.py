import threading
import pytest
from end_point_blank.request_store import RequestStore


@pytest.fixture(autouse=True)
def clear_store():
    RequestStore.clear()
    yield
    RequestStore.clear()


def test_set_and_get():
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
    RequestStore.set(environ)
    assert RequestStore.get() is environ


def test_get_returns_none_when_not_set():
    assert RequestStore.get() is None


def test_clear_removes_stored_environ():
    RequestStore.set({"PATH_INFO": "/"})
    RequestStore.clear()
    assert RequestStore.get() is None


def test_is_thread_local():
    environ = {"PATH_INFO": "/main"}
    RequestStore.set(environ)

    results = []

    def thread_target():
        results.append(RequestStore.get())

    t = threading.Thread(target=thread_target)
    t.start()
    t.join()

    assert results[0] is None, "Other threads should not see the main thread's environ"
    assert RequestStore.get() is environ, "Main thread environ should still be set"
