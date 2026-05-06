"""Config-driven anomaly rule engine (v1.5).

Reads a list of `RuleConfig` entries and applies them to incoming ROS
messages. Each rule watches one topic and one (possibly nested) field;
fires when the configured condition holds continuously for ≥ duration.

The engine receives the deserialized message object directly from the
ros_bridge callback path — no CDR walking needed. Field access is
`getattr` with dot-paths.

Same `update + duration + cooldown` discipline as v1's hardcoded
detectors. Customer-facing semantics are identical to those.

Pure logic; no rclpy.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..config import RuleConfig

log = logging.getLogger(__name__)


@dataclass
class RuleAnomaly:
    name: str
    detected_at_ns: int
    started_at_ns: int
    matched_value: Any


class _RuleState:
    __slots__ = ("condition_started_at", "last_fire_ns", "warned_missing_field")

    def __init__(self) -> None:
        self.condition_started_at: int | None = None
        self.last_fire_ns: int = -10**18
        self.warned_missing_field: bool = False


def _read_field(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _evaluate(rule: RuleConfig, value: Any) -> bool:
    if rule.equals is not None:
        return value == rule.equals
    if rule.not_equals is not None:
        return value != rule.not_equals
    if rule.lt is not None:
        return value < rule.lt
    if rule.gt is not None:
        return value > rule.gt
    if rule.lte is not None:
        return value <= rule.lte
    if rule.gte is not None:
        return value >= rule.gte
    return False  # validator should prevent reaching here


class RuleEngine:
    """Applies config-driven rules. One callback fires per anomaly."""

    def __init__(
        self,
        rules: Iterable[RuleConfig],
        on_anomaly: Callable[[RuleAnomaly], None],
    ) -> None:
        self._rules = list(rules)
        self._on_anomaly = on_anomaly
        self._states: dict[str, _RuleState] = {r.name: _RuleState() for r in self._rules}
        self._by_topic: dict[str, list[RuleConfig]] = {}
        for r in self._rules:
            self._by_topic.setdefault(r.topic, []).append(r)
        self._lock = threading.Lock()

    @property
    def topics(self) -> list[str]:
        """Topics any rule listens on. main.py uses this to register callbacks."""
        return list(self._by_topic.keys())

    def update(self, topic: str, msg: Any, ts_ns: int) -> None:
        rules = self._by_topic.get(topic)
        if not rules:
            return
        for rule in rules:
            try:
                value = _read_field(msg, rule.field)
            except (AttributeError, TypeError):
                state = self._states[rule.name]
                if not state.warned_missing_field:
                    log.warning(
                        "rule %r: field %r not found on topic %s; rule will be silent",
                        rule.name, rule.field, rule.topic,
                    )
                    state.warned_missing_field = True
                continue

            matched = _evaluate(rule, value)
            self._apply(rule, matched, value, ts_ns)

    def _apply(self, rule: RuleConfig, matched: bool, value: Any, ts_ns: int) -> None:
        state = self._states[rule.name]
        anomaly: RuleAnomaly | None = None
        with self._lock:
            if not matched:
                state.condition_started_at = None
                return
            if state.condition_started_at is None:
                state.condition_started_at = ts_ns
            elapsed_ns = ts_ns - state.condition_started_at
            duration_ns = int(rule.duration_seconds * 1e9)
            if elapsed_ns < duration_ns:
                return
            cooldown_ns = int(rule.cooldown_seconds * 1e9)
            if ts_ns - state.last_fire_ns < cooldown_ns:
                return
            anomaly = RuleAnomaly(
                name=rule.name,
                detected_at_ns=ts_ns,
                started_at_ns=state.condition_started_at,
                matched_value=value,
            )
            state.last_fire_ns = ts_ns
            state.condition_started_at = None

        log.warning(
            "Rule %r fired on %s: %s = %r",
            rule.name, rule.topic, rule.field, value,
        )
        try:
            self._on_anomaly(anomaly)
        except Exception:
            log.exception("on_anomaly callback raised")
            raise
