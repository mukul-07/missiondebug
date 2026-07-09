"""Duplicate YAML keys in the agent config: load still succeeds with YAML's
last-wins semantics, but the agent names the duplicates in a warning.

Regression for the field report where a second `http_host: "0.0.0.0"` pasted
above the shipped `http_host: "127.0.0.1"` was silently ignored — the agent
stayed on loopback and the hub could not reach it back.
"""

import logging

from missiondebug_agent.config import AgentConfig

MINIMAL = """
robot_id: "robot-001"
topics:
  - {{ name: "/cmd_vel", type: "geometry_msgs/msg/Twist" }}
{extra}
"""


def _write(tmp_path, extra: str):
    p = tmp_path / "config.yaml"
    p.write_text(MINIMAL.format(extra=extra))
    return p


def test_duplicate_http_host_warns_and_last_wins(tmp_path, caplog):
    p = _write(
        tmp_path,
        'http_host: "0.0.0.0"\n'
        'http_host: "127.0.0.1"\n',
    )
    with caplog.at_level(logging.WARNING, logger="missiondebug_agent.config"):
        cfg = AgentConfig.load(p)

    assert cfg.http_host == "127.0.0.1"  # YAML last-wins, unchanged
    warnings = [r for r in caplog.records if "duplicate key" in r.getMessage()]
    assert len(warnings) == 1
    assert "http_host" in warnings[0].getMessage()


def test_clean_config_does_not_warn(tmp_path, caplog):
    p = _write(tmp_path, 'http_host: "0.0.0.0"\n')
    with caplog.at_level(logging.WARNING, logger="missiondebug_agent.config"):
        cfg = AgentConfig.load(p)

    assert cfg.http_host == "0.0.0.0"
    assert not [r for r in caplog.records if "duplicate key" in r.getMessage()]


def test_merge_keys_do_not_false_positive(tmp_path, caplog):
    # A `<<:` anchor overlapping an explicit key is legitimate YAML usage,
    # not an operator mistake — no warning.
    p = tmp_path / "config.yaml"
    p.write_text(
        "robot_id: robot-001\n"
        "topics:\n"
        '  - { name: "/cmd_vel", type: "geometry_msgs/msg/Twist" }\n'
        "_base: &base\n"
        "  http_port: 7000\n"
        "hub_defaults:\n"
        "  <<: *base\n"
        "  http_port: 7001\n"
    )
    with caplog.at_level(logging.WARNING, logger="missiondebug_agent.config"):
        AgentConfig.load(p)

    assert not [r for r in caplog.records if "duplicate key" in r.getMessage()]


def test_nested_duplicate_is_reported_too(tmp_path, caplog):
    p = _write(
        tmp_path,
        "hub:\n"
        '  url: "http://old:8000"\n'
        '  url: "http://new:8000"\n',
    )
    with caplog.at_level(logging.WARNING, logger="missiondebug_agent.config"):
        cfg = AgentConfig.load(p)

    assert cfg.hub.url == "http://new:8000"
    warnings = [r for r in caplog.records if "duplicate key" in r.getMessage()]
    assert len(warnings) == 1
    assert "url" in warnings[0].getMessage()
