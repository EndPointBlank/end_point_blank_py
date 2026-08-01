"""
Contract: Configuration.worker_count controls how many background threads
DelayedWriter spawns to drain its queue. Before this test existed, the wiring
between worker_count and the writer's thread pool had no regression coverage.
"""

from unittest.mock import MagicMock

import pytest

import end_point_blank as epb
from end_point_blank.configuration import Configuration
from end_point_blank.writers.delayed_writer import DelayedWriter


@pytest.fixture(autouse=True)
def reset_config():
    config = Configuration()
    config._init_defaults()
    yield
    config._init_defaults()


def test_default_worker_count_spawns_four_threads():
    writer = DelayedWriter("log_url")
    assert len(writer._workers) == 4
    assert all(t.is_alive() for t in writer._workers)


def test_custom_worker_count_is_honored():
    Configuration().worker_count = 2
    writer = DelayedWriter("log_url")
    assert len(writer._workers) == 2
    assert all(t.is_alive() for t in writer._workers)


def test_configure_worker_count_is_honored_end_to_end():
    epb.configure(worker_count=7)
    writer = DelayedWriter("log_url")
    assert len(writer._workers) == 7


def test_worker_threads_are_daemons_so_they_dont_block_process_exit():
    writer = DelayedWriter("log_url")
    assert all(t.daemon for t in writer._workers)


class TestDelivery:
    """The queue exists so the request thread never waits on intake. What it
    must not do is lose payloads, or let one failed flush stop the drain."""

    def _writer(self, worker_count=1):
        Configuration().worker_count = worker_count
        writer = DelayedWriter("log_url")
        writer._direct = MagicMock()
        return writer

    def test_every_queued_payload_is_delivered(self):
        writer = self._writer()

        writer.write([{"n": n} for n in range(10)])
        writer._queue.join()

        delivered = [p for call in writer._direct.write.call_args_list for p in call[0][0]]
        assert sorted(p["n"] for p in delivered) == list(range(10))

    def test_no_request_carries_more_than_a_full_batch(self):
        # Batching is what keeps the queue draining faster than it fills; an
        # unbounded batch would put the whole backlog in one oversized request.
        writer = self._writer()

        writer.write([{"n": n} for n in range(10)])
        writer._queue.join()

        assert all(len(call[0][0]) <= 4 for call in writer._direct.write.call_args_list)

    def test_a_failed_flush_does_not_stop_the_worker(self):
        # A transient intake outage must cost the payloads in flight, not every
        # payload for the remaining life of the process.
        writer = self._writer()
        writer._direct.write.side_effect = [RuntimeError("no route"), None]

        writer.write([{"n": 1}])
        writer._queue.join()
        writer.write([{"n": 2}])
        writer._queue.join()

        assert writer._direct.write.call_count == 2

    def test_a_full_queue_drops_payloads_rather_than_blocking(self):
        # The alternative is back-pressure onto the request thread: a slow intake
        # would then slow down the application it is only observing.
        writer = self._writer(worker_count=0)

        writer.write([{"n": n} for n in range(600)])

        assert writer._queue.qsize() == 500
