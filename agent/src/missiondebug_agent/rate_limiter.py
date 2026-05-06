"""Per-topic rate-limiter for the ROS bridge.

Pure logic, no rclpy — testable in isolation.
"""

from __future__ import annotations

import threading


class RateLimiter:
    """Decides whether to keep or drop a message on a topic, given the
    topic's `divisor` (1 = keep all, N = keep every Nth).

    Keeps message #0, #N, #2N, ... — predictable across restarts within
    the lifetime of an instance.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def should_keep(self, topic: str, divisor: int) -> bool:
        if divisor <= 1:
            return True
        with self._lock:
            n = self._counts.get(topic, 0)
            self._counts[topic] = n + 1
        return n % divisor == 0
