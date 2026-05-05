"""Thread-safe rolling ring buffer storing serialized ROS messages as bytes.

Per SPEC: bytes only — no message deserialization in the buffer hot path.
Eviction is by wall-clock window (buffer_seconds).
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BufferedMessage:
    timestamp_ns: int  # monotonic ns from time.monotonic_ns()
    wall_ns: int       # wall-clock ns (time.time_ns()) for session metadata
    topic: str
    payload: bytes


class RingBuffer:
    """Time-windowed deque of (timestamp, topic, bytes) entries.

    O(1) append. O(n) snapshot. Eviction occurs on append, dropping any
    entries older than (latest - window_ns).
    """

    def __init__(self, window_seconds: float) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._window_ns = int(window_seconds * 1e9)
        self._items: deque[BufferedMessage] = deque()
        self._lock = threading.Lock()

    @property
    def window_ns(self) -> int:
        return self._window_ns

    def append(self, msg: BufferedMessage) -> None:
        with self._lock:
            self._items.append(msg)
            cutoff = msg.timestamp_ns - self._window_ns
            while self._items and self._items[0].timestamp_ns < cutoff:
                self._items.popleft()

    def snapshot(self) -> list[BufferedMessage]:
        """Return a shallow copy of the current buffer contents."""
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
