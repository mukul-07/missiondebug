"""ROS 2 topic discovery for the control API.

Lists the topics currently visible on the robot's ROS graph, with their message
type, whether that type is resolvable (importable) here, and whether it is a
"recommended" topic to capture (a common control/telemetry signal).

This is deliberately INDEPENDENT of the capture path. The agent has two capture
engines: a C++ module (the default, faster) and a Python RosBridge fallback. Only
the Python path holds an rclpy Node; on the C++ path there is no Python node at
all. So discovery uses its OWN short-lived rclpy node and tears it down after,
which means GET /topics works the same on both paths.

Discovery is best-effort: ROS graph discovery (DDS) is eventually-consistent, so a
topic may briefly show no type, and a freshly-started publisher may not appear yet.
We return what is visible and never raise to the caller.
"""

from __future__ import annotations

import re

from .ros_bridge import _resolve_msg_type

# Heuristic "recommended to capture" patterns: common control + telemetry topics
# across ground robots, drones (mavros), and manipulators. Transparent and static
# (NOT ML): a topic is recommended when its name matches one of these. The reason
# string is surfaced so the UI can explain "why recommended".
_RECOMMENDED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/cmd_vel$"), "velocity command"),
    (re.compile(r"/odom$|/odometry"), "odometry"),
    (re.compile(r"/scan$|/laser"), "laser scan"),
    (re.compile(r"/tf$|/tf_static$"), "transforms"),
    (re.compile(r"/battery|/power"), "battery / power"),
    (re.compile(r"/imu"), "IMU"),
    (re.compile(r"/joint_states$|/joint_trajectory"), "joint states"),
    (re.compile(r"/wrench|/ft_sensor"), "force / torque"),
    (re.compile(r"/plan$|/path$|/global_plan"), "planned path"),
    (re.compile(r"/mavros/state$"), "flight state"),
    (re.compile(r"/mavros/.*(position|velocity|altitude|global)"), "flight telemetry"),
    (re.compile(r"/image|/camera"), "camera"),
]


def _recommendation(name: str) -> str | None:
    for pat, reason in _RECOMMENDED:
        if pat.search(name):
            return reason
    return None


def _is_resolvable(type_str: str) -> bool:
    """True if the message type can be imported here (e.g. its package is built
    and sourced). px4_msgs and other unbuilt custom types resolve to False, which
    is exactly the silent-skip case we want to surface to the operator."""
    try:
        _resolve_msg_type(type_str)
        return True
    except Exception:
        return False


_HIDDEN = {"/parameter_events", "/rosout"}


def classify_topics(names_and_types: list[tuple[str, list[str]]]) -> list[dict]:
    """Pure: turn raw (name, [types]) graph output into the discovery dicts.

    Filters hidden topics, takes the first type, flags resolvable + recommended,
    and sorts by name. Separated from the ROS plumbing so it is unit-testable
    without a running graph.
    """
    out: list[dict] = []
    for name, types in names_and_types:
        if name in _HIDDEN:
            continue
        type_str = types[0] if types else ""
        reason = _recommendation(name)
        out.append({
            "name": name,
            "type": type_str,
            "resolvable": _is_resolvable(type_str) if type_str else False,
            "recommended": reason is not None,
            "reason": reason,
        })
    out.sort(key=lambda t: t["name"])
    return out


def discover_topics(timeout_s: float = 1.0) -> list[dict]:
    """Return the visible ROS 2 topics as a sorted list of dicts:
        {name, type, resolvable, recommended (bool), reason (str|None)}

    Hidden topics (parameter_events, rosout) are filtered out. A topic with
    multiple types reports the first; one with no type yet is kept with an empty
    type and resolvable=False. Never raises: returns [] if rclpy is unavailable.
    """
    try:
        import rclpy
        from rclpy.node import Node
    except Exception:
        return []

    created_context = False
    node: "Node | None" = None
    try:
        if not rclpy.ok():
            rclpy.init()
            created_context = True
        node = rclpy.create_node("missiondebug_discovery")

        # Give DDS a brief moment to populate the graph, then read it.
        end = node.get_clock().now().nanoseconds + int(timeout_s * 1e9)
        names_and_types: list[tuple[str, list[str]]] = []
        while True:
            names_and_types = node.get_topic_names_and_types()
            if names_and_types or node.get_clock().now().nanoseconds >= end:
                break
            rclpy.spin_once(node, timeout_sec=0.1)

        return classify_topics(names_and_types)
    except Exception:
        return []
    finally:
        try:
            if node is not None:
                node.destroy_node()
        except Exception:
            pass
        try:
            if created_context and rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass
