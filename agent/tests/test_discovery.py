"""discovery: classify_topics turns raw ROS graph output into the discovery
dicts (resolvable + recommended), without needing a running ROS graph."""

import missiondebug_agent.discovery as disc
from missiondebug_agent.discovery import (
    _recommendation,
    classify_topics,
)


def test_recommendation_matches_common_signals():
    assert _recommendation("/cmd_vel") == "velocity command"
    assert _recommendation("/odom") == "odometry"
    assert _recommendation("/scan") == "laser scan"
    assert _recommendation("/mavros/state") == "flight state"
    assert _recommendation("/joint_states") == "joint states"
    # a namespaced cmd_vel still matches
    assert _recommendation("/robot1/cmd_vel") == "velocity command"


def test_recommendation_none_for_unknown_topic():
    assert _recommendation("/some/random/debug_topic") is None


def test_classify_filters_hidden_topics():
    raw = [
        ("/parameter_events", ["rcl_interfaces/msg/ParameterEvent"]),
        ("/rosout", ["rcl_interfaces/msg/Log"]),
        ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
    ]
    names = [t["name"] for t in classify_topics(raw)]
    assert names == ["/cmd_vel"]  # hidden ones dropped, the rest kept


def test_classify_sorts_by_name():
    raw = [
        ("/zzz", ["std_msgs/msg/String"]),
        ("/aaa", ["std_msgs/msg/String"]),
        ("/mmm", ["std_msgs/msg/String"]),
    ]
    assert [t["name"] for t in classify_topics(raw)] == ["/aaa", "/mmm", "/zzz"]


def test_classify_marks_recommended_with_reason():
    out = classify_topics([("/cmd_vel", ["geometry_msgs/msg/Twist"])])
    assert out[0]["recommended"] is True
    assert out[0]["reason"] == "velocity command"

    out = classify_topics([("/debug/foo", ["std_msgs/msg/String"])])
    assert out[0]["recommended"] is False
    assert out[0]["reason"] is None


def test_classify_empty_type_is_unresolvable():
    # a topic visible on the graph but with no type yet (DDS eventual consistency)
    out = classify_topics([("/half_discovered", [])])
    assert out[0]["type"] == ""
    assert out[0]["resolvable"] is False


def test_classify_resolvable_flag(monkeypatch):
    # _is_resolvable imports the message package, which is not present off-robot,
    # so stub it to assert classify wires the flag through (the px4 case = False).
    def fake_resolvable(type_str):
        return type_str.startswith("geometry_msgs") or type_str.startswith("std_msgs")

    monkeypatch.setattr(disc, "_is_resolvable", fake_resolvable)
    out = classify_topics([
        ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
        ("/fmu/out/vehicle_odometry", ["px4_msgs/msg/VehicleOdometry"]),
    ])
    by_name = {t["name"]: t for t in out}
    assert by_name["/cmd_vel"]["resolvable"] is True
    # px4_msgs not built -> surfaced as unresolvable, not silently skipped
    assert by_name["/fmu/out/vehicle_odometry"]["resolvable"] is False


def test_classify_takes_first_type_when_multiple():
    out = classify_topics([("/multi", ["a_msgs/msg/A", "b_msgs/msg/B"])])
    assert out[0]["type"] == "a_msgs/msg/A"


def test_discover_topics_returns_empty_without_ros(monkeypatch):
    # off-robot (no rclpy) the function must return [] and never raise.
    import builtins

    real_import = builtins.__import__

    def no_rclpy(name, *args, **kwargs):
        if name == "rclpy" or name.startswith("rclpy."):
            raise ImportError("rclpy not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_rclpy)
    assert disc.discover_topics() == []
