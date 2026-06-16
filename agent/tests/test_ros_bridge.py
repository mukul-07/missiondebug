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
