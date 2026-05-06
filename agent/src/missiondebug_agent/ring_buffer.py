"""Per-topic time-windowed ring buffer storing serialized ROS messages as bytes.

v1.5 redesign: each topic has its own deque + window. A burst of camera
frames can't evict /robot_state_fms history. Optional global byte budget
acts as a backstop when many high-rate topics share the same agent.

Per SPEC: bytes only — no message deserialization in the buffer hot path.
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


class _TopicBuffer:
    __slots__ = ("window_ns", "items", "total_bytes")

    def __init__(self, window_ns: int) -> None:
        self.window_ns = window_ns
        self.items: deque[BufferedMessage] = deque()
        self.total_bytes: int = 0

    def append(self, msg: BufferedMessage) -> None:
        self.items.append(msg)
        self.total_bytes += len(msg.payload)
        cutoff = msg.timestamp_ns - self.window_ns
        while self.items and self.items[0].timestamp_ns < cutoff:
            dropped = self.items.popleft()
            self.total_bytes -= len(dropped.payload)


class RingBuffer:
    """Time-windowed buffers, one deque per topic.

    O(1) append per topic. O(n) snapshot (sorted across topics).
    Eviction is window-based per topic, plus an optional global byte cap
    that drops oldest entries across all topics when exceeded.

    Backward-compatible with v1: callers that only pass `window_seconds`
    get a single shared default window for every topic.
    """

    def __init__(
        self,
        window_seconds: float,
        max_total_bytes: int | None = None,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._default_window_ns = int(window_seconds * 1e9)
        self._max_total_bytes = max_total_bytes
        self._topics: dict[str, _TopicBuffer] = {}
        # `_topic_overrides` records explicitly-configured windows (vs
        # default for unknown topics). Configured windows survive across
        # `clear()`; unknown-topic buffers are recreated on demand.
        self._topic_overrides: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def window_ns(self) -> int:
        """Default window for topics that haven't been explicitly configured."""
        return self._default_window_ns

    def configure_topic(self, name: str, window_seconds: float | None = None) -> None:
        """Pre-register a topic with its own window. Optional.

        Topics that aren't pre-registered get the default window on first append.
        """
        window_ns = (
            int(window_seconds * 1e9) if window_seconds is not None else self._default_window_ns
        )
        if window_ns <= 0:
            raise ValueError("window_seconds must be > 0 if provided")
        with self._lock:
            self._topic_overrides[name] = window_ns
            if name in self._topics:
                self._topics[name].window_ns = window_ns

    def append(self, msg: BufferedMessage) -> None:
        with self._lock:
            buf = self._topics.get(msg.topic)
            if buf is None:
                window = self._topic_overrides.get(msg.topic, self._default_window_ns)
                buf = _TopicBuffer(window)
                self._topics[msg.topic] = buf
            buf.append(msg)
            if self._max_total_bytes is not None:
                self._enforce_global_budget_locked()

    def snapshot(self) -> list[BufferedMessage]:
        """All buffered messages across all topics, sorted by timestamp."""
        with self._lock:
            merged: list[BufferedMessage] = []
            for buf in self._topics.values():
                merged.extend(buf.items)
        merged.sort(key=lambda m: m.timestamp_ns)
        return merged

    def __len__(self) -> int:
        with self._lock:
            return sum(len(buf.items) for buf in self._topics.values())

    def total_bytes(self) -> int:
        with self._lock:
            return sum(buf.total_bytes for buf in self._topics.values())

    def topic_size(self, topic: str) -> int:
        """Number of messages currently buffered on a given topic (0 if unknown)."""
        with self._lock:
            buf = self._topics.get(topic)
            return len(buf.items) if buf else 0

    def clear(self) -> None:
        with self._lock:
            self._topics.clear()

    # ----- internal -----

    def _enforce_global_budget_locked(self) -> None:
        """Drop oldest entries across all topics until total <= cap.

        Called with self._lock held. O(K * dropped) where K is topic count;
        in practice topic counts are small (<100) so this is fine.
        """
        cap = self._max_total_bytes
        assert cap is not None
        total = sum(buf.total_bytes for buf in self._topics.values())
        while total > cap:
            oldest_topic: str | None = None
            oldest_ts = None
            for name, buf in self._topics.items():
                if not buf.items:
                    continue
                head_ts = buf.items[0].timestamp_ns
                if oldest_ts is None or head_ts < oldest_ts:
                    oldest_ts = head_ts
                    oldest_topic = name
            if oldest_topic is None:
                break  # nothing left to drop
            buf = self._topics[oldest_topic]
            dropped = buf.items.popleft()
            buf.total_bytes -= len(dropped.payload)
            total -= len(dropped.payload)
