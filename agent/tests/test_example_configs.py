"""The shipped example configs must actually load + validate — so the
robot-type claims in examples/README.md (ground vehicle, drone, manipulator,
forklift) are real, not aspirational. This guards them in CI.
"""

from pathlib import Path

import pytest
import yaml

from missiondebug_agent.config import AgentConfig, RuleConfig

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

# Full agent configs (one per robot type + the demo).
FULL_CONFIGS = [
    "ground-vehicle-config.yaml",   # AMRs, delivery bots, indoor service
    "drone-config.yaml",            # UAV via mavros
    "manipulator-config.yaml",      # robot arm + MoveIt2
    "forklift-config.yaml",         # warehouse forklift (68-topic reference)
    "demo-config.yaml",
]


@pytest.mark.parametrize("name", FULL_CONFIGS)
def test_example_config_loads_and_validates(name):
    cfg = AgentConfig.load(EXAMPLES / name)

    # Has topics to capture.
    assert cfg.topics, f"{name}: no topics configured"

    # Every rule parses + passes the exactly-one-condition validator
    # (all_rules() = explicit rules + the desugared battery_low rule).
    rules = cfg.anomaly.all_rules()

    # No dead detectors: every rule fires on a topic the agent subscribes to.
    subscribed = {t.name for t in cfg.topics}
    dead = {r.topic for r in rules} - subscribed
    assert not dead, f"{name}: rules reference unsubscribed topics {dead} (dead detectors)"


def test_rule_patterns_cookbook_all_valid():
    """rule-patterns.yaml is a copy-paste cookbook of rule recipes (not a full
    config) — every recipe in it must be a valid RuleConfig."""
    data = yaml.safe_load((EXAMPLES / "rule-patterns.yaml").read_text())
    rules = data.get("rules", [])
    assert len(rules) >= 3, "cookbook should have several recipes"
    for rd in rules:
        RuleConfig.model_validate(rd)  # raises if any recipe is malformed


def test_every_robot_type_in_readme_has_a_config():
    """The README advertises four robot types — each must have a real config."""
    readme = (EXAMPLES / "README.md").read_text().lower()
    for kind, fname in [
        ("ground", "ground-vehicle-config.yaml"),
        ("drone", "drone-config.yaml"),
        ("manipulator", "manipulator-config.yaml"),
    ]:
        assert kind in readme, f"README doesn't mention {kind}"
        assert (EXAMPLES / fname).exists(), f"missing {fname}"
