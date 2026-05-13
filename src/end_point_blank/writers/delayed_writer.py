from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Dict, List

from ..configuration import Configuration
from .direct_writer import DirectWriter

logger = logging.getLogger(__name__)

_BATCH_SIZE = 4


class DelayedWriter:
    """
    Asynchronous writer that queues payloads and flushes them in background threads.

    Batches up to 4 payloads per HTTP request to reduce overhead.
    Equivalent to the Ruby gem's ``EndPointBlank::DelayedWriter``.
    """

    def __init__(self, url_key: str) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=500)
        self._direct = DirectWriter(url_key)
        worker_count = Configuration().worker_count
        self._workers: List[threading.Thread] = []
        for _ in range(worker_count):
            t = threading.Thread(target=self._process, daemon=True)
            t.start()
            self._workers.append(t)

    def write(self, payloads: List[Dict[str, Any]]) -> None:
        """Enqueues *payloads* for asynchronous delivery, dropping if the queue is full."""
        for payload in payloads:
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                logger.warning("DelayedWriter queue full, dropping payload")

    def _process(self) -> None:
        while True:
            try:
                first = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            batch = [first]
            # Drain additional items up to BATCH_SIZE
            for _ in range(_BATCH_SIZE - 1):
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            try:
                self._direct.write(batch)
            except Exception as exc:
                logger.error("DelayedWriter flush error: %s", exc)
            finally:
                for _ in batch:
                    self._queue.task_done()
