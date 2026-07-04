"""`missiondebug-agent init` — the setup wizard, tested without ROS.

Discovery is injected (monkeypatched module function); the interactive loop
runs through scripted input; written configs must round-trip through the
real AgentConfig loader.
"""

from __future__ import annotations

import missiondebug_agent.init_wizard as wiz
from missiondebug_agent.config import AgentConfig
from missiondebug_agent.init_wizard import (
    ARCHETYPES,
    build_anomaly_block,
    parse_toggle,
    propose_selection,
    render_config,
)

TOPICS = [
    {"name": "/cmd_vel", "type": "geometry_msgs/msg/Twist", "resolvable": True,
     "category": "control", "recommended": True, "reason": "x", "large": False, "publishers": 1},
    {"name": "/odom", "type": "nav_msgs/msg/Odometry", "resolvable": True,
     "category": "state", "recommended": True, "reason": "x", "large": False, "publishers": 1},
    {"name": "/fmu/out/vehicle_odometry", "type": "px4_msgs/msg/VehicleOdometry",
     "resolvable": False, "category": "other", "recommended": False, "reason": None,
     "large": False, "publishers": 1},
    {"name": "/robot_debug", "type": "std_msgs/msg/String", "resolvable": True,
     "category": "debug", "recommended": False, "reason": None, "large": False, "publishers": 1},
]


def test_propose_selection_recommended_and_resolvable_only():
    # /cmd_vel + /odom recommended&resolvable; px4 unresolvable; debug unrecommended
    assert propose_selection(TOPICS) == {"/cmd_vel", "/odom"}
    # a recommended-but-unbuilt topic must NOT be pre-checked
    px4_recommended = [dict(TOPICS[2], recommended=True)]
    assert propose_selection(px4_recommended) == set()


def test_parse_toggle():
    assert parse_toggle("", 4) is None            # accept
    assert parse_toggle("a", 4) == "a"
    assert parse_toggle("none", 4) == "n"
    assert parse_toggle("1,3", 4) == [0, 2]
    assert parse_toggle("2 4", 4) == [1, 3]
    assert parse_toggle("0,5,junk,2", 4) == [1]   # out-of-range + junk dropped


def test_build_anomaly_block_filters_to_chosen_topics():
    # Everything selected -> full ground-vehicle preset
    full = build_anomaly_block("ground-vehicle", {"/cmd_vel", "/odom", "/battery", "/scan"})
    assert "stall:" in full and "battery_low:" in full and "topic_dropout:" in full
    # No /odom -> stall (needs both) is dropped; no /battery, no /scan
    trimmed = build_anomaly_block("ground-vehicle", {"/cmd_vel"})
    assert "stall:" not in trimmed
    assert "battery_low:" not in trimmed
    assert "topic_dropout:" not in trimmed
    # Nothing applicable -> empty string, config omits the anomaly key
    assert build_anomaly_block("manipulator", {"/tf"}) == ""


def test_render_config_roundtrips_through_agent_config(tmp_path):
    content = render_config(
        robot_id="bot-7",
        topics=[("/cmd_vel", "geometry_msgs/msg/Twist"), ("/odom", "nav_msgs/msg/Odometry")],
        output_dir=str(tmp_path / "sessions"),
        hub_url="http://hub:8000",
        agent_url="http://10.0.0.7:7000",
        subsystem="navigation",
        anomaly_block=build_anomaly_block("ground-vehicle", {"/cmd_vel", "/odom"}),
    )
    p = tmp_path / "config.yaml"
    p.write_text(content)
    cfg = AgentConfig.load(p)
    assert cfg.robot_id == "bot-7"
    assert [t.name for t in cfg.topics] == ["/cmd_vel", "/odom"]
    assert cfg.http_host == "0.0.0.0"      # fleet mode binds beyond loopback
    assert cfg.hub.url == "http://hub:8000"
    assert cfg.hub.agent_url == "http://10.0.0.7:7000"
    assert cfg.hub.subsystem == "navigation"
    # stall needs /cmd_vel + /odom: both chosen, so it's present
    assert cfg.anomaly.stall is not None


def test_render_config_standalone_stays_loopback(tmp_path):
    content = render_config(
        robot_id="solo", topics=[("/cmd_vel", "geometry_msgs/msg/Twist")],
        output_dir=str(tmp_path), hub_url=None, agent_url=None, subsystem=None,
        anomaly_block="",
    )
    p = tmp_path / "config.yaml"
    p.write_text(content)
    cfg = AgentConfig.load(p)
    assert cfg.http_host == "127.0.0.1"
    assert cfg.hub.url is None


def _run(monkeypatch, tmp_path, argv, answers=None, topics=TOPICS, settled=True):
    """Drive wiz.main with fake discovery + scripted answers; returns
    (exit_code, printed_lines, config_path)."""
    monkeypatch.setattr(wiz, "_scan", lambda: (topics, settled))
    monkeypatch.setattr(wiz, "primary_ip", lambda: "10.1.2.3")
    monkeypatch.setattr(wiz.shutil, "which", lambda _: None)  # no systemd prompt
    printed: list[str] = []
    answers = list(answers or [])

    def fake_input(prompt=""):
        return answers.pop(0) if answers else ""

    def fake_print(*a, **k):
        printed.append(" ".join(str(x) for x in a))

    cfg = tmp_path / "config.yaml"
    rc = wiz.main([*argv, "--config", str(cfg)], input_fn=fake_input, print_fn=fake_print)
    return rc, printed, cfg


def test_yes_mode_writes_recommended_config(monkeypatch, tmp_path):
    rc, printed, cfg = _run(monkeypatch, tmp_path, ["--yes", "--hub-url", "http://hub:8000"])
    assert rc == 0
    loaded = AgentConfig.load(cfg)
    assert {t.name for t in loaded.topics} == {"/cmd_vel", "/odom"}
    assert loaded.hub.url == "http://hub:8000"
    assert loaded.hub.agent_url == "http://10.1.2.3:7000"
    assert loaded.robot_id  # hostname default, non-empty


def test_interactive_toggle_and_confirm(monkeypatch, tmp_path):
    # robot id -> "amr-9"; toggle 4 (debug topic ON); accept; hub skip; confirm write
    rc, printed, cfg = _run(
        monkeypatch, tmp_path, [],
        answers=["amr-9", "4", "", "", "y"],
    )
    assert rc == 0
    loaded = AgentConfig.load(cfg)
    assert loaded.robot_id == "amr-9"
    assert {t.name for t in loaded.topics} == {"/cmd_vel", "/odom", "/robot_debug"}
    assert loaded.hub.url is None


def test_no_ros_falls_back_to_archetype(monkeypatch, tmp_path):
    rc, printed, cfg = _run(
        monkeypatch, tmp_path, ["--yes", "--archetype", "drone"],
        topics=[], settled=True,
    )
    assert rc == 0
    loaded = AgentConfig.load(cfg)
    assert {t.name for t in loaded.topics} == {t[0] for t in ARCHETYPES["drone"]["topics"]}
    # drone preset rules that reference selected topics made it in
    assert loaded.anomaly.battery_low is not None


def test_abort_at_confirm_writes_nothing(monkeypatch, tmp_path):
    rc, printed, cfg = _run(
        monkeypatch, tmp_path, [],
        answers=["", "", "", "n"],   # id default, accept topics, skip hub, refuse write
    )
    assert rc == 1
    assert not cfg.exists()


def test_existing_config_backed_up(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("robot_id: old\n")
    rc, printed, _ = _run(monkeypatch, tmp_path, ["--yes"])
    assert rc == 0
    backups = list(tmp_path.glob("config.yaml.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "robot_id: old\n"


def test_unresolvable_selection_warns(monkeypatch, tmp_path):
    # toggle px4 topic (3) on, accept, skip hub, confirm
    rc, printed, cfg = _run(
        monkeypatch, tmp_path, [],
        answers=["", "3", "", "", "y"],
    )
    assert rc == 0
    joined = "\n".join(printed)
    assert "unbuilt message types" in joined
    assert "/fmu/out/vehicle_odometry" in joined
