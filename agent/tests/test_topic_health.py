"""Configured-topic health (rides the hub heartbeat), tested without ROS."""

from __future__ import annotations

from missiondebug_agent.topic_health import compute_topics_health

CONFIGURED = [
    ("/cmd_vel", "geometry_msgs/msg/Twist"),
    ("/odom", "nav_msgs/msg/Odometry"),
    ("/thing", "custom_msgs/msg/Thing"),
]


def _scan_of(*topics):
    return lambda: {"settled": True, "topics": list(topics)}


def _resolve(unresolvable=()):
    return lambda t: t not in unresolvable


def test_all_healthy():
    scan = _scan_of(
        {"name": "/cmd_vel", "publishers": 1},
        {"name": "/odom", "publishers": 2},
        {"name": "/thing", "publishers": 1},
    )
    h = compute_topics_health(CONFIGURED, scan=scan, resolve=_resolve())
    assert h == {"ok": 3, "missing": [], "silent": [], "unresolvable": []}


def test_buckets():
    scan = _scan_of(
        {"name": "/cmd_vel", "publishers": 1},
        {"name": "/odom", "publishers": 0},   # ghost: advertised, nothing publishing
        # /thing not on the graph at all
    )
    h = compute_topics_health(
        CONFIGURED, scan=scan, resolve=_resolve(unresolvable=("custom_msgs/msg/Thing",))
    )
    assert h["ok"] == 1
    assert h["silent"] == ["/odom"]
    # unresolvable wins over missing: the type check already dooms the capture
    assert h["unresolvable"] == ["/thing"]
    assert h["missing"] == []


def test_missing_when_resolvable_but_absent():
    scan = _scan_of({"name": "/cmd_vel", "publishers": 1})
    h = compute_topics_health(CONFIGURED[:2], scan=scan, resolve=_resolve())
    assert h["missing"] == ["/odom"]


def test_unsettled_scan_returns_none():
    def scan():
        return {"settled": False, "topics": [{"name": "/cmd_vel", "publishers": 1}]}
    assert compute_topics_health(CONFIGURED, scan=scan, resolve=_resolve()) is None


def test_scan_error_returns_none():
    def scan():
        raise RuntimeError("rclpy died")

    assert compute_topics_health(CONFIGURED, scan=scan, resolve=_resolve()) is None


def test_empty_config_returns_none():
    assert compute_topics_health([], scan=_scan_of(), resolve=_resolve()) is None


def test_list_cap():
    configured = [(f"/t{i}", "std_msgs/msg/String") for i in range(30)]
    h = compute_topics_health(configured, scan=_scan_of(), resolve=_resolve())
    assert len(h["missing"]) == 20  # capped so heartbeats stay small
    assert h["ok"] == 0
