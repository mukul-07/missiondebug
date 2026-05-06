from missiondebug_agent.config import AnomalyConfig, BatteryLowConfig
from missiondebug_agent.detectors.battery_low import to_rule


def test_to_rule_basic():
    cfg = BatteryLowConfig(topic="/battery", threshold=0.20)
    r = to_rule(cfg)
    assert r.name == "battery_low"
    assert r.topic == "/battery"
    assert r.field == "percentage"
    assert r.lt == 0.20
    assert r.duration_seconds == 5.0
    assert r.cooldown_seconds == 600.0


def test_to_rule_overrides():
    cfg = BatteryLowConfig(
        topic="/forklift/bms",
        threshold=0.10,
        field="state_of_charge",
        duration_seconds=2.0,
        cooldown_seconds=300.0,
        name="critical-batt",
    )
    r = to_rule(cfg)
    assert r.name == "critical-batt"
    assert r.field == "state_of_charge"
    assert r.lt == 0.10
    assert r.duration_seconds == 2.0
    assert r.cooldown_seconds == 300.0


def test_anomaly_config_all_rules_includes_battery_low():
    cfg = AnomalyConfig(
        battery_low=BatteryLowConfig(topic="/battery"),
    )
    rules = cfg.all_rules()
    assert len(rules) == 1
    assert rules[0].name == "battery_low"
    assert rules[0].lt == 0.20


def test_anomaly_config_all_rules_no_battery_low():
    cfg = AnomalyConfig()
    assert cfg.all_rules() == []


def test_anomaly_config_all_rules_combines():
    """battery_low desugars and is appended to user-written rules."""
    from missiondebug_agent.config import RuleConfig

    cfg = AnomalyConfig(
        rules=[RuleConfig(name="r1", topic="/x", field="data", equals=1)],
        battery_low=BatteryLowConfig(topic="/battery"),
    )
    rules = cfg.all_rules()
    names = [r.name for r in rules]
    assert names == ["r1", "battery_low"]
