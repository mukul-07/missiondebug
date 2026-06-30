"""discovery: classify_topics sorts ROS topics into signal categories with a
recommend default + reason, without needing a running ROS graph."""

import missiondebug_agent.discovery as disc
from missiondebug_agent.discovery import _classify, classify_topics


def test_classify_control():
    cat, rec, _ = _classify("/cmd_vel", "geometry_msgs/msg/Twist")
    assert cat == "control" and rec is True


def test_classify_state_by_type():
    # name unknown but the type says Odometry -> state, recommended
    cat, rec, _ = _classify("/wheel_telemetry", "nav_msgs/msg/Odometry")
    assert cat == "state" and rec is True


def test_classify_safety():
    cat, rec, _ = _classify("/battery", "sensor_msgs/msg/BatteryState")
    assert cat == "safety" and rec is True


def test_classify_perception_and_large():
    cat, rec, _ = _classify("/camera/image_raw/compressed", "sensor_msgs/msg/CompressedImage")
    assert cat == "perception" and rec is True


def test_classify_transform():
    cat, rec, _ = _classify("/tf", "tf2_msgs/msg/TFMessage")
    assert cat == "transform" and rec is True


def test_classify_plan():
    cat, rec, _ = _classify("/plan", "nav_msgs/msg/Path")
    assert cat == "plan" and rec is True


def test_classify_debug_not_recommended():
    cat, rec, _ = _classify("/random_debug", "std_msgs/msg/String")
    assert cat == "debug" and rec is False


def test_classify_unknown_is_other_unchecked():
    # a custom topic we do not recognize: shown but NOT pre-checked
    cat, rec, reason = _classify("/my_proprietary_status", "custom_msgs/msg/Status")
    assert cat == "other" and rec is False and reason is None


def test_debug_wins_over_other_patterns():
    # a name ending in _debug must be debug even if it carries a state-ish type
    cat, rec, _ = _classify("/odom_debug", "nav_msgs/msg/Odometry")
    assert cat == "debug" and rec is False


def test_classify_topics_shape_and_fields():
    raw = [
        ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
        ("/camera/image_raw", ["sensor_msgs/msg/Image"]),
        ("/random_debug", ["std_msgs/msg/String"]),
        ("/parameter_events", ["rcl_interfaces/msg/ParameterEvent"]),  # hidden
    ]
    out = classify_topics(raw)
    names = [t["name"] for t in out]
    assert "/parameter_events" not in names, "hidden topics dropped"
    by = {t["name"]: t for t in out}
    assert by["/cmd_vel"]["category"] == "control" and by["/cmd_vel"]["recommended"] is True
    assert by["/camera/image_raw"]["large"] is True, "Image flagged large"
    assert by["/random_debug"]["recommended"] is False
    # every dict carries the full field set the card relies on
    for t in out:
        assert set(t) >= {"name", "type", "resolvable", "category", "recommended", "reason", "large"}


def test_classify_empty_type_unresolvable():
    out = classify_topics([("/half", [])])
    assert out[0]["type"] == "" and out[0]["resolvable"] is False


def test_discover_topics_returns_empty_without_ros(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_rclpy(name, *a, **k):
        if name == "rclpy" or name.startswith("rclpy."):
            raise ImportError("no rclpy")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_rclpy)
    assert disc.discover_topics() == []
