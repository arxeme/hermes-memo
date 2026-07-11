"""At-least-once capture delivery.

The adapter contract (Memo TD §4.1 原文完整性) puts the "delivered"
responsibility on the adapter: buffer locally and retry with backoff until
the server acks with accepted/duplicated counts. Events freeze their
synthesized channel_msg_ref at enqueue time, so every retry is idempotent
on the server's uniq(scope, conversation, channel_msg_ref) key.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List

logger = logging.getLogger("hermes.memory.memo.capture")


class CaptureWorker:
    """Background delivery queue: enqueue() never blocks the turn; a daemon
    thread drains batches with exponential backoff on failure."""

    def __init__(
        self,
        send: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
        *,
        batch_max: int = 20,
        retry_base_seconds: float = 1.0,
        retry_cap_seconds: float = 60.0,
        buffer_max_events: int = 2000,
    ) -> None:
        self._send = send
        self._batch_max = batch_max
        self._retry_base = retry_base_seconds
        self._retry_cap = retry_cap_seconds
        self._buffer_max = buffer_max_events
        self._buffer: Deque[Dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = False
        self._failures = 0
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="memo-capture", daemon=True)
        self._thread.start()

    # -- producer side -------------------------------------------------------

    def enqueue(self, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        with self._lock:
            for ev in events:
                if len(self._buffer) >= self._buffer_max:
                    # Bounded buffer: drop oldest, keep newest (a hole in the
                    # backup beats unbounded memory in the agent process).
                    self._buffer.popleft()
                    self._dropped += 1
                self._buffer.append(ev)
            self._idle.clear()
        if self._dropped:
            logger.warning("memo capture buffer overflow: %d events dropped so far", self._dropped)
        self._wake.set()

    def flush(self, timeout: float) -> bool:
        """Block until the buffer drains or timeout; used at compression and
        shutdown boundaries so RecentRaw sees the latest turns."""
        self._wake.set()
        return self._idle.wait(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        self.flush(timeout)
        self._stop = True
        self._wake.set()
        self._thread.join(timeout=1.0)

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)

    # -- consumer side -------------------------------------------------------

    def _run(self) -> None:
        while not self._stop:
            self._wake.wait()
            self._wake.clear()
            while not self._stop:
                with self._lock:
                    if not self._buffer:
                        self._idle.set()
                        break
                    batch = [self._buffer[i] for i in range(min(self._batch_max, len(self._buffer)))]
                try:
                    self._send(batch)
                except Exception as e:
                    self._failures += 1
                    delay = min(self._retry_base * (2 ** min(self._failures - 1, 10)), self._retry_cap)
                    logger.warning("memo capture delivery failed (retry in %.1fs): %s", delay, e)
                    if self._wake.wait(delay):
                        self._wake.clear()
                    continue
                self._failures = 0
                with self._lock:
                    for _ in range(len(batch)):
                        if self._buffer:
                            self._buffer.popleft()
