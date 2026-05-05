"""Path-deviation anomaly detector.

Watches a planned path (e.g. nav_msgs/msg/Path) and the robot's pose.
Fires if the perpendicular distance from the robot to the nearest
segment of the plan exceeds `threshold_meters` for at least
`duration_seconds` continuously. Cooldown prevents re-firing.

Pure logic — no rclpy. The agent's main loop wires real ROS messages
to `update_plan(waypoints)` and `update_pose(x, y, ts_ns)`.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

log = logging.getLogger(__name__)

Point = tuple[float, float]


@dataclass
class PathDeviationAnomaly:
    distance_m: float
    detected_at_ns: int
    started_at_ns: int


def _distance_to_segment(px: float, py: float, ax: float, ay: float,
                         bx: float, by: float) -> float:
    """Perpendicular distance from P to segment AB (clamped to endpoints)."""
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def min_distance_to_path(px: float, py: float, waypoints: Sequence[Point]) -> float:
    """Min distance from P to any segment in the polyline `waypoints`."""
    if len(waypoints) < 2:
        # Degenerate: distance to the single point, or +inf if empty.
        if not waypoints:
            return math.inf
        ax, ay = waypoints[0]
        return math.hypot(px - ax, py - ay)
    best = math.inf
    for (ax, ay), (bx, by) in zip(waypoints[:-1], waypoints[1:]):
        d = _distance_to_segment(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return best


class PathDeviationDetector:
    def __init__(
        self,
        threshold_meters: float,
        duration_seconds: float,
        cooldown_seconds: float,
        on_anomaly: Callable[[PathDeviationAnomaly], None],
    ) -> None:
        if threshold_meters <= 0:
            raise ValueError("threshold_meters must be > 0")
        self._threshold_m = threshold_meters
        self._duration_ns = int(duration_seconds * 1e9)
        self._cooldown_ns = int(cooldown_seconds * 1e9)
        self._on_anomaly = on_anomaly
        self._waypoints: list[Point] = []
        self._dev_started_at: int | None = None
        self._last_fire_ns: int = -10**18
        self._lock = threading.Lock()

    def update_plan(self, waypoints: Sequence[Point]) -> None:
        with self._lock:
            self._waypoints = list(waypoints)
            # New plan resets the deviation clock — the previous drift was
            # against a different reference.
            self._dev_started_at = None

    def update_pose(self, x: float, y: float, ts_ns: int) -> None:
        with self._lock:
            if not self._waypoints:
                return
            distance = min_distance_to_path(x, y, self._waypoints)
            if distance <= self._threshold_m:
                self._dev_started_at = None
                return
            if self._dev_started_at is None:
                self._dev_started_at = ts_ns
                return
            elapsed = ts_ns - self._dev_started_at
            if elapsed < self._duration_ns:
                return
            if ts_ns - self._last_fire_ns < self._cooldown_ns:
                return
            anomaly = PathDeviationAnomaly(
                distance_m=distance,
                detected_at_ns=ts_ns,
                started_at_ns=self._dev_started_at,
            )
            self._last_fire_ns = ts_ns
            self._dev_started_at = None

        log.warning(
            "Path-deviation anomaly: drifted %.3fm for %.2fs",
            anomaly.distance_m,
            (anomaly.detected_at_ns - anomaly.started_at_ns) / 1e9,
        )
        try:
            self._on_anomaly(anomaly)
        except Exception:
            log.exception("on_anomaly callback raised")
            raise
