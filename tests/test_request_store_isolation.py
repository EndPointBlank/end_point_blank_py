"""
Request-to-request isolation.

WSGI servers reuse threads, so the question is not "can it store a value" but
"can one request read another request's value". Holding per-request data in a
``threading.local`` made that possible: it was correct only while something
reliably cleared it. Keying off the WSGI environ rules it out.

The sequential test with *no* clear in between is the one that matters —
isolation must not depend on cleanup running.
"""

import threading

import pytest

from end_point_blank.request_store import RequestStore


@pytest.fixture(autouse=True)
def _clean_store():
    RequestStore.clear()
    yield
    RequestStore.clear()


class TestSequentialRequestsOnTheSameThread:
    def test_a_later_request_sees_nothing_from_an_earlier_one(self):
        RequestStore.set({})
        RequestStore.set_source_application_environment_id("env-first")
        RequestStore.set_deprecation({"deprecated_at": "2026-01-01T00:00:00Z"})

        # The next request installs its own environ. Deliberately no clear() —
        # that is the point.
        RequestStore.set({})

        assert RequestStore.get_source_application_environment_id() is None
        assert RequestStore.get_deprecation() is None

    def test_a_stranded_value_cannot_be_read_by_the_next_request(self):
        # Simulate the gap the thread-local had: something set a value outside
        # any middleware run, so the `finally` never happened for it.
        RequestStore.set({})
        RequestStore.set_source_application_environment_id("env-stranded")

        RequestStore.set({})

        assert RequestStore.get_source_application_environment_id() is None

    def test_each_request_gets_its_own_uuid(self):
        RequestStore.set({})
        first = RequestStore.get_uuid()

        RequestStore.set({})
        second = RequestStore.get_uuid()

        assert first is not None
        assert second is not None
        assert first != second


class TestConcurrentRequestsOnDifferentThreads:
    def test_threads_do_not_see_each_other(self):
        seen = {}
        both_wrote = threading.Barrier(2, timeout=5)

        def worker(name):
            RequestStore.set({})
            RequestStore.set_source_application_environment_id(name)

            # Both threads write before either reads, so a shared slot would be
            # visibly wrong rather than accidentally right through timing.
            both_wrote.wait()

            seen[name] = RequestStore.get_source_application_environment_id()
            RequestStore.clear()

        threads = [threading.Thread(target=worker, args=(n,)) for n in ("one", "two")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert seen == {"one": "one", "two": "two"}

    def test_a_reused_thread_starts_clean(self):
        # A pool hands the same thread to a second request. Under the old
        # thread-local this is exactly where the previous caller's data leaked.
        results = []

        def handle(name):
            RequestStore.set({})
            results.append(RequestStore.get_source_application_environment_id())
            RequestStore.set_source_application_environment_id(name)

        thread = threading.Thread(target=lambda: [handle("first"), handle("second")])
        thread.start()
        thread.join(timeout=5)

        # Neither request saw anything on arrival.
        assert results == [None, None]


class TestOutsideARequest:
    def test_setting_is_a_no_op_rather_than_an_error(self):
        RequestStore.clear()

        RequestStore.set_source_application_environment_id("env-123")
        RequestStore.set_deprecation({"deprecated_at": "2026-01-01T00:00:00Z"})

        assert RequestStore.get_source_application_environment_id() is None
        assert RequestStore.get_deprecation() is None

    def test_clear_drops_everything(self):
        RequestStore.set({})
        RequestStore.set_source_application_environment_id("env-123")
        RequestStore.set_deprecation({"deprecated_at": "2026-01-01T00:00:00Z"})

        RequestStore.clear()

        assert RequestStore.get() is None
        assert RequestStore.get_uuid() is None
        assert RequestStore.get_source_application_environment_id() is None
        assert RequestStore.get_deprecation() is None
