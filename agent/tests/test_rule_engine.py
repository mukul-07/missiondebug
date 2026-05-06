"""Tests for the config-driven rule engine (v1.5 Phase 2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from missiondebug_agent.config import RuleConfig
from missiondebug_agent.detectors.from_config import RuleAnomaly, RuleEngine


def _msg(**kw) -> SimpleNamespace:
    """Build a fake ROS message with attribute access."""
    return SimpleNamespace(**kw)


def _ns(seconds: float) -> int:
    return int(seconds * 1e9)


# ---------- RuleConfig validation ----------


def test_rule_requires_exactly_one_condition():
    with pytest.raises(ValueError):
        RuleConfig(name="x", topic="/t", field="a")
    with pytest.raises(ValueError):
        RuleConfig(name="x", topic="/t", field="a", equals=1, lt=2)


def test_rule_accepts_each_condition_kind():
    for kw in [{"equals": 1}, {"not_equals": 1}, {"lt": 1}, {"gt": 1},
               {"lte": 1}, {"gte": 1}]:
        RuleConfig(name="x", topic="/t", field="a", **kw)


# ---------- evaluation ----------


def test_state_equality_fires_after_duration():
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(
        name="fsm-error", topic="/state", field="data",
        equals="error", duration_seconds=2.0, cooldown_seconds=10.0,
    )]
    eng = RuleEngine(rules, fired.append)

    eng.update("/state", _msg(data="ok"), _ns(0))
    eng.update("/state", _msg(data="error"), _ns(1.0))      # condition starts
    eng.update("/state", _msg(data="error"), _ns(1.5))      # not yet
    assert not fired
    eng.update("/state", _msg(data="error"), _ns(3.0))      # 2s elapsed
    assert len(fired) == 1
    assert fired[0].name == "fsm-error"
    assert fired[0].matched_value == "error"


def test_condition_resets_when_value_changes():
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(
        name="x", topic="/t", field="data",
        equals="bad", duration_seconds=2.0,
    )]
    eng = RuleEngine(rules, fired.append)

    eng.update("/t", _msg(data="bad"), _ns(0))
    eng.update("/t", _msg(data="ok"), _ns(1.0))     # reset
    eng.update("/t", _msg(data="bad"), _ns(2.0))    # fresh start
    eng.update("/t", _msg(data="bad"), _ns(3.5))    # only 1.5s of bad
    assert not fired
    eng.update("/t", _msg(data="bad"), _ns(4.5))    # 2.5s of bad -> fire
    assert len(fired) == 1


def test_cooldown_blocks_refire():
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(
        name="x", topic="/t", field="data",
        equals=True, duration_seconds=0.0, cooldown_seconds=10.0,
    )]
    eng = RuleEngine(rules, fired.append)

    eng.update("/t", _msg(data=True), _ns(0))           # fires immediately
    assert len(fired) == 1
    eng.update("/t", _msg(data=False), _ns(1.0))        # reset
    eng.update("/t", _msg(data=True), _ns(2.0))         # within cooldown
    assert len(fired) == 1
    eng.update("/t", _msg(data=True), _ns(11.0))        # past cooldown
    assert len(fired) == 2


def test_numeric_threshold_lt():
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(
        name="low-batt", topic="/bms", field="percentage",
        lt=0.20, duration_seconds=1.0, cooldown_seconds=5.0,
    )]
    eng = RuleEngine(rules, fired.append)

    eng.update("/bms", _msg(percentage=0.50), _ns(0))    # ok
    eng.update("/bms", _msg(percentage=0.15), _ns(1.0))  # below threshold
    eng.update("/bms", _msg(percentage=0.10), _ns(2.5))  # 1.5s elapsed
    assert len(fired) == 1
    assert fired[0].matched_value == 0.10


def test_numeric_threshold_gt():
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(
        name="overspeed", topic="/imu", field="accel",
        gt=10.0, duration_seconds=0.0,
    )]
    eng = RuleEngine(rules, fired.append)
    eng.update("/imu", _msg(accel=12.0), _ns(0))
    assert fired and fired[0].matched_value == 12.0


def test_dotted_field_path():
    """Rules can read nested fields like status.status."""
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(
        name="nav-aborted", topic="/result", field="status.status",
        equals=4, duration_seconds=0.0,
    )]
    eng = RuleEngine(rules, fired.append)

    msg = _msg(status=_msg(status=4, text="ABORTED"))
    eng.update("/result", msg, _ns(0))
    assert len(fired) == 1


def test_missing_field_silently_ignored():
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(
        name="ghost", topic="/t", field="nonexistent.path",
        equals=1, duration_seconds=0.0,
    )]
    eng = RuleEngine(rules, fired.append)
    eng.update("/t", _msg(other="hello"), _ns(0))
    assert not fired


def test_multiple_rules_on_same_topic_independent():
    fired: list[RuleAnomaly] = []
    rules = [
        RuleConfig(name="a", topic="/t", field="x", equals=1, duration_seconds=0.0),
        RuleConfig(name="b", topic="/t", field="y", lt=0, duration_seconds=0.0),
    ]
    eng = RuleEngine(rules, fired.append)

    eng.update("/t", _msg(x=1, y=5), _ns(0))    # only "a" fires
    assert len(fired) == 1 and fired[0].name == "a"
    eng.update("/t", _msg(x=2, y=-1), _ns(10.0))  # only "b" fires
    assert len(fired) == 2 and fired[1].name == "b"


def test_unrelated_topic_ignored():
    fired: list[RuleAnomaly] = []
    rules = [RuleConfig(name="x", topic="/a", field="data", equals=1, duration_seconds=0.0)]
    eng = RuleEngine(rules, fired.append)
    eng.update("/b", _msg(data=1), _ns(0))   # different topic
    assert not fired


def test_topics_property():
    rules = [
        RuleConfig(name="a", topic="/x", field="d", equals=1),
        RuleConfig(name="b", topic="/y", field="d", equals=1),
        RuleConfig(name="c", topic="/x", field="d", equals=2),
    ]
    eng = RuleEngine(rules, lambda _a: None)
    assert set(eng.topics) == {"/x", "/y"}
