"""ros_bridge: one unresolvable topic type must not crash the agent."""

import pytest

from missiondebug_agent.config import TopicConfig
from missiondebug_agent.ros_bridge import RosBridge, _resolve_msg_type


def test_resolve_rejects_malformed_type():
    with pytest.raises(ValueError):
        _resolve_msg_type("not-a-valid-type")


def test_resolve_raises_on_missing_package():
    with pytest.raises(ModuleNotFoundError):
        _resolve_msg_type("nonexistent_pkg/msg/Foo")


def test_subscribe_skips_unresolvable_topic():
    # __new__ bypasses __init__ (which needs rclpy, robot-only). The skip path
    # returns before touching any rclpy state, so a bare instance is enough.
    bridge = RosBridge.__new__(RosBridge)
    bridge._subs = []
    # missing package (e.g. moveit_msgs on a robot without MoveIt): skip, no raise
    bridge._subscribe(TopicConfig(name="/move_group/result",
                                  type="moveit_msgs/msg/MoveGroupActionResult"))
    # malformed type string: also skipped, not raised
    bridge._subscribe(TopicConfig(name="/y", type="not-a-valid-type"))
    assert bridge._subs == []  # both skipped, no subscription, no crash


def test_qos_default_is_int_depth():
    # The default (no reliability set) path returns a plain int depth, which
    # rclpy treats as KEEP_LAST with default reliability. No rclpy import needed,
    # so this is the testable path off-robot.
    bridge = RosBridge.__new__(RosBridge)
    assert bridge._qos_for(TopicConfig(name="/a", type="std_msgs/msg/String")) == 10
    assert bridge._qos_for(
        TopicConfig(name="/a", type="std_msgs/msg/String", queue_depth=3)
    ) == 3
    # reliability="reliable" is still the default int path (no special profile)
    assert bridge._qos_for(
        TopicConfig(name="/a", type="std_msgs/msg/String", reliability="reliable")
    ) == 10


def test_topic_config_cpu_defaults_preserve_behavior():
    # New CPU fields default to the prior behavior: depth 10, no explicit
    # reliability (so the int-depth path), keep-all rate.
    t = TopicConfig(name="/a", type="std_msgs/msg/String")
    assert t.queue_depth == 10
    assert t.reliability is None
    assert t.rate_divisor == 1
