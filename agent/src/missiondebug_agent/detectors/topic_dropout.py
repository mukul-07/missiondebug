"""Topic-dropout detector (v1.5).

Fires when a watched topic hasn't received a message for `silence_seconds`.
Captures the previous 60s — useful when a sensor publisher dies silently
and you want to see what was happening right before it stopped.

Rule-engine rules can't express this: they only run on incoming messages,
so absence-of-message is invisible to them. This detector polls instead.

The polling cadence is exposed as `tick(now_ns)` so tests can drive it
without threads. In production, main.py runs a daemon thread that calls
tick() periodically.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from ..config import TopicDropoutConfig

log = logging.getLogger(__name__)


@dataclass
class DropoutAnomaly:
    topic: str
    name: str
    silence_ns: int
    detected_at_ns: int


class TopicDropoutDetector:
    def __init__(
        self,
        configs: Iterable[TopicDropoutConfig],
        on_anomaly: Callable[[DropoutAnomaly], None],
    ) -> None:
        self._configs: dict[str, TopicDropoutConfig] = {c.topic: c for c in configs}
        self._on_anomaly = on_anomaly
        self._last_seen: dict[str, int] = {}
        self._last_fired: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def topics(self) -> list[str]:
        return list(self._configs.keys())

    def saw(self, topic: str, ts_ns: int) -> None:
        """Called from each ros_bridge message callback for a watched topic."""
        with self._lock:
            self._last_seen[topic] = ts_ns

    def tick(self, now_ns: int) -> list[DropoutAnomaly]:
        """One check pass. Returns and dispatches anomalies that fired this pass.

        Public for testability — tests call it directly with synthetic times.
        """
        fires: list[DropoutAnomaly] = []
        with self._lock:
            for topic, cfg in self._configs.items():
                last = self._last_seen.get(topic)
                if last is None:
                    # Never seen — don't fire until we've seen at least one
                    # message. Avoids spurious fires at agent startup.
                    continue
                silence_ns = int(cfg.silence_seconds * 1e9)
                gap = now_ns - last
                if gap < silence_ns:
                    continue
                last_fire = self._last_fired.get(topic, -10**18)
                cooldown_ns = int(cfg.cooldown_seconds * 1e9)
                if now_ns - last_fire < cooldown_ns:
                    continue
                self._last_fired[topic] = now_ns
                fires.append(DropoutAnomaly(
                    topic=topic,
                    name=cfg.name or f"dropout:{topic}",
                    silence_ns=gap,
                    detected_at_ns=now_ns,
                ))

        for a in fires:
            log.warning(
                "Topic-dropout %r: no message for %.2fs",
                a.topic, a.silence_ns / 1e9,
            )
            try:
                self._on_anomaly(a)
            except Exception:
                log.exception("on_anomaly raised for dropout %r", a.topic)
        return fires

    # ---- background thread (production use) ----

    def start(self, check_interval_seconds: float = 1.0) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(check_interval_seconds):
                try:
                    self.tick(time.monotonic_ns())
                except Exception:
                    log.exception("dropout tick failed")

        self._thread = threading.Thread(
            target=loop, name="dropout-watcher", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
