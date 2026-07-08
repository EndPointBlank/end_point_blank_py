"""
Contract: Configuration.worker_count controls how many background threads
DelayedWriter spawns to drain its queue. Before this test existed, the wiring
between worker_count and the writer's thread pool had no regression coverage.
"""

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
