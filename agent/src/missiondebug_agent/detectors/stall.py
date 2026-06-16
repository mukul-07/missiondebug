"""Stall anomaly detector.

A stall is "commanded to move but not actually moving" held for
`duration_seconds`. We compare the COMMANDED velocity (from /cmd_vel) against
the ACTUAL velocity (from odometry). This distinguishes a real stall (told to
move, not moving) from a robot that is legitimately parked (not told to move),
which would otherwise fire continuously while idle.

Both signals must be fresh (seen within `stale_seconds`) for the comparison to
be trusted: if odometry has gone silent that is a dropout, not a stall, and we
do not fire on stale data. After firing, hold a cooldown so we don't re-fire on
the same continuous stall.

Decoupled from rclpy so it's unit-testable. The agent main loop wires the
/cmd_vel callback to `update_cmd(linear_x, angular_z, ts_ns)` and the odometry
callback to `update_motion(linear, angular, ts_ns)`, both using the same clock.
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
        motion_threshold: float | None = None,
        stale_seconds: float = 1.0,
    ) -> None:
        self._cmd_thresh = velocity_threshold
        self._motion_thresh = (
            motion_threshold if motion_threshold is not None else velocity_threshold
        )
        self._duration_ns = int(duration_seconds * 1e9)
        self._cooldown_ns = int(cooldown_seconds * 1e9)
        self._stale_ns = int(stale_seconds * 1e9)
        self._on_stall = on_stall

        self._cmd_moving = False
        self._cmd_ts: int | None = None
        self._motion_moving: bool | None = None
        self._motion_ts: int | None = None
        self._stall_started_at: int | None = None
        self._last_fire_ns: int = -10**18  # very far in the past
        self._lock = threading.Lock()

    def update_cmd(self, linear_x: float, angular_z: float, ts_ns: int) -> None:
        """Latest commanded velocity (from /cmd_vel)."""
        with self._lock:
            self._cmd_moving = (
                abs(linear_x) >= self._cmd_thresh or abs(angular_z) >= self._cmd_thresh
            )
            self._cmd_ts = ts_ns
            anomaly = self._evaluate_locked(ts_ns)
        if anomaly is not None:
            self._fire(anomaly)

    def update_motion(self, linear: float, angular: float, ts_ns: int) -> None:
        """Latest actual velocity (from odometry)."""
        with self._lock:
            self._motion_moving = (
                abs(linear) >= self._motion_thresh or abs(angular) >= self._motion_thresh
            )
            self._motion_ts = ts_ns
            anomaly = self._evaluate_locked(ts_ns)
        if anomaly is not None:
            self._fire(anomaly)

    def _evaluate_locked(self, ts_ns: int) -> StallAnomaly | None:
        cmd_fresh = self._cmd_ts is not None and ts_ns - self._cmd_ts <= self._stale_ns
        motion_fresh = (
            self._motion_ts is not None and ts_ns - self._motion_ts <= self._stale_ns
        )
        # Stall = commanded to move, but odometry says not moving. Both signals
        # must be fresh, and we need an actual-velocity reading (motion_moving is
        # not None) to confirm "not moving" — never infer it from missing data.
        stalling = (
            cmd_fresh
            and motion_fresh
            and self._cmd_moving
            and self._motion_moving is False
        )
        if not stalling:
            self._stall_started_at = None
            return None
        if self._stall_started_at is None:
            self._stall_started_at = ts_ns
            return None
        if ts_ns - self._stall_started_at < self._duration_ns:
            return None
        if ts_ns - self._last_fire_ns < self._cooldown_ns:
            return None
        anomaly = StallAnomaly(started_at_ns=self._stall_started_at, detected_at_ns=ts_ns)
        self._last_fire_ns = ts_ns
        # Reset so the next stall after cooldown starts a fresh clock.
        self._stall_started_at = None
        return anomaly

    def _fire(self, anomaly: StallAnomaly) -> None:
        log.warning(
            "Stall anomaly detected: commanded to move but not moving for %d ns",
            anomaly.detected_at_ns - anomaly.started_at_ns,
        )
        try:
            self._on_stall(anomaly)
        except Exception:
            log.exception("on_stall callback raised")
            raise
