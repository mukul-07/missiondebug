"""Cross-topic comparison engine (v2).

Compares a field on one topic against a field on another and fires when they
disagree for >= duration_seconds. Unlike the single-topic rule engine, a
compare rule references TWO signals, so it can express "commanded X but actual Y
diverges" anomalies (told-to-move-not-moving, tracking error, grasp failure).

Both operands must be fresh (seen within stale_seconds) for the comparison to be
trusted: a stale signal is a dropout, not a divergence, and we do not fire on it.
Same update + duration + cooldown discipline as the other detectors.

Pure logic; no rclpy.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..config import CompareRuleConfig

log = logging.getLogger(__name__)


@dataclass
class CompareAnomaly:
    name: str
    detected_at_ns: int
    started_at_ns: int
    a_value: Any
    b_value: Any


def _read_field(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _condition(rule: CompareRuleConfig, a: Any, b: Any) -> bool:
    try:
        a = float(a)
        b = float(b)
    except (TypeError, ValueError):
        return False
    if rule.op == "a_active_b_idle":
        return abs(a) >= rule.threshold and abs(b) < rule.threshold
    if rule.op == "diverge_gt":
        return abs(a - b) > rule.threshold
    return False


class _RuleState:
    __slots__ = (
        "a_value", "a_ts", "b_value", "b_ts",
        "condition_started_at", "last_fire_ns", "warned_missing_field",
    )

    def __init__(self) -> None:
        self.a_value: Any = None
        self.a_ts: int | None = None
        self.b_value: Any = None
        self.b_ts: int | None = None
        self.condition_started_at: int | None = None
        self.last_fire_ns: int = -10**18
        self.warned_missing_field: bool = False


class CompareEngine:
    """Applies config-driven cross-topic comparison rules."""

    def __init__(
        self,
        rules: Iterable[CompareRuleConfig],
        on_anomaly: Callable[[CompareAnomaly], None],
    ) -> None:
        self._rules = list(rules)
        self._on_anomaly = on_anomaly
        self._states: dict[str, _RuleState] = {r.name: _RuleState() for r in self._rules}
        self._by_topic: dict[str, list[CompareRuleConfig]] = {}
        for r in self._rules:
            for t in {r.a.topic, r.b.topic}:
                self._by_topic.setdefault(t, []).append(r)
        self._lock = threading.Lock()

    @property
    def topics(self) -> list[str]:
        """Topics any rule listens on (a or b). main.py subscribes to these."""
        return list(self._by_topic.keys())

    def update(self, topic: str, msg: Any, ts_ns: int) -> None:
        rules = self._by_topic.get(topic)
        if not rules:
            return
        anomalies: list[CompareAnomaly] = []
        with self._lock:
            for rule in rules:
                st = self._states[rule.name]
                try:
                    if rule.a.topic == topic:
                        st.a_value = _read_field(msg, rule.a.field)
                        st.a_ts = ts_ns
                    if rule.b.topic == topic:
                        st.b_value = _read_field(msg, rule.b.field)
                        st.b_ts = ts_ns
                except (AttributeError, TypeError):
                    if not st.warned_missing_field:
                        log.warning(
                            "compare rule %r: a field was not found on %s; rule will be silent",
                            rule.name, topic,
                        )
                        st.warned_missing_field = True
                    continue
                anomaly = self._evaluate(rule, st, ts_ns)
                if anomaly is not None:
                    anomalies.append(anomaly)
        for anomaly in anomalies:
            log.warning(
                "Compare rule %r fired: a=%r b=%r",
                anomaly.name, anomaly.a_value, anomaly.b_value,
            )
            try:
                self._on_anomaly(anomaly)
            except Exception:
                log.exception("on_anomaly callback raised")
                raise

    def _evaluate(
        self, rule: CompareRuleConfig, st: _RuleState, ts_ns: int
    ) -> CompareAnomaly | None:
        if st.a_ts is None or st.b_ts is None:
            return None
        stale_ns = int(rule.stale_seconds * 1e9)
        if ts_ns - st.a_ts > stale_ns or ts_ns - st.b_ts > stale_ns:
            st.condition_started_at = None
            return None
        if not _condition(rule, st.a_value, st.b_value):
            st.condition_started_at = None
            return None
        if st.condition_started_at is None:
            st.condition_started_at = ts_ns
        if ts_ns - st.condition_started_at < int(rule.duration_seconds * 1e9):
            return None
        if ts_ns - st.last_fire_ns < int(rule.cooldown_seconds * 1e9):
            return None
        anomaly = CompareAnomaly(
            name=rule.name,
            detected_at_ns=ts_ns,
            started_at_ns=st.condition_started_at,
            a_value=st.a_value,
            b_value=st.b_value,
        )
        st.last_fire_ns = ts_ns
        st.condition_started_at = None
        return anomaly
