"""Stall anomaly detector.

If linear.x and angular.z are both below threshold for >= stall_duration,
fire 'stall' anomaly. After firing, hold a cooldown so we don't re-fire
on the same continuous stall.

Decoupled from rclpy so it's unit-testable. The agent main loop wires the
/cmd_vel subscriber callback to call `update(linear_x, angular_z, ts_ns)`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class StallAnomaly:
    started_at_ns: int
    detected_at_ns: int


class StallDetector:
    def __init__(
        self,
        velocity_threshold: float,
        duration_seconds: float,
        cooldown_seconds: float,
        on_stall: Callable[[StallAnomaly], None],
    ) -> None:
        self._vthresh = velocity_threshold
        self._duration_ns = int(duration_seconds * 1e9)
        self._cooldown_ns = int(cooldown_seconds * 1e9)
        self._on_stall = on_stall
        self._stall_started_at: int | None = None
        self._last_fire_ns: int = -10**18  # very far in the past
        self._lock = threading.Lock()

    def update(self, linear_x: float, angular_z: float, ts_ns: int) -> None:
        moving = abs(linear_x) >= self._vthresh or abs(angular_z) >= self._vthresh
        with self._lock:
            if moving:
                self._stall_started_at = None
                return
            if self._stall_started_at is None:
                self._stall_started_at = ts_ns
                return
            elapsed = ts_ns - self._stall_started_at
            if elapsed < self._duration_ns:
                return
            if ts_ns - self._last_fire_ns < self._cooldown_ns:
                return
            anomaly = StallAnomaly(
                started_at_ns=self._stall_started_at,
                detected_at_ns=ts_ns,
            )
            self._last_fire_ns = ts_ns
            # Reset so the next stall after cooldown starts a fresh clock.
            self._stall_started_at = None

        log.warning(
            "Stall anomaly detected (started %d ns ago)", anomaly.detected_at_ns - anomaly.started_at_ns
        )
        try:
            self._on_stall(anomaly)
        except Exception:
            log.exception("on_stall callback raised")
            raise
