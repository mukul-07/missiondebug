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
# Signal-type classifier (NOT ML). Each topic is sorted into a CATEGORY by its
# name and/or message type. The category sets a default "recommend" (whether the
# capture checkbox starts on) and a plain-language reason the UI can show. The goal
# is "we pre-checked the signals that usually matter for an incident; adjust if you
# want" -- recommend by KIND, we do not claim to know the anomaly.
#
# Order matters: the first matching rule wins. Rules can match on name (n), type
# (t), or both. category is one of:
#   control state safety perception transform plan debug other
# recommend=True categories pre-check; debug/other start unchecked (shown, never
# hidden -- the user decides on anything we do not recognize).
_CATEGORIES = [
    # category, recommend, reason, name_regex|None, type_regex|None
    ("debug", False, "diagnostic / log topic, usually not needed",
        re.compile(r"/rosout|/parameter_events|/diagnostics_agg|_debug$|/debug"), None),
    ("transform", True, "coordinate transforms, needed to interpret other topics",
        re.compile(r"/tf$|/tf_static$"), re.compile(r"tf2_msgs/")),
    ("control", True, "control command (what the robot was told to do)",
        re.compile(r"/cmd_vel|/cmd_|/setpoint|joint_trajectory|/command"),
        re.compile(r"Twist|JointTrajectory|AckermannDrive")),
    ("safety", True, "safety / health signal (a common cause in incidents)",
        re.compile(r"/battery|/power|/diagnostics$|/emergency|/estop|/fault"),
        re.compile(r"BatteryState|DiagnosticArray")),
    ("state", True, "robot state / telemetry (what the robot actually did)",
        re.compile(r"/odom|/odometry|/joint_states|/imu|/pose|/wrench|/ft_sensor|"
                   r"/mavros/(state|local_position|global_position|altitude|imu)"),
        re.compile(r"Odometry|JointState|Imu|PoseStamped|WrenchStamped|NavSatFix")),
    ("plan", True, "plan / goal (intent, to compare against what happened)",
        re.compile(r"/plan$|/path$|/global_plan|/goal|/waypoints"),
        re.compile(r"nav_msgs/msg/Path")),
    ("perception", True, "perception / sensor stream (often large)",
        re.compile(r"/scan|/laser|/image|/camera|/points|/pointcloud|/depth"),
        re.compile(r"LaserScan|Image|CompressedImage|PointCloud2")),
]

# Message types that tend to be high-rate / large; flagged so the UI can warn
# "capturing this will grow your file".
_LARGE_TYPE = re.compile(r"Image|PointCloud2|CompressedImage|LaserScan")


def _classify(name: str, type_str: str) -> tuple[str, bool, str | None]:
    """Return (category, recommend, reason) for a topic. Unrecognized -> ('other',
    False, None): shown to the user but not pre-checked."""
    for category, recommend, reason, n_re, t_re in _CATEGORIES:
        if (n_re and n_re.search(name)) or (t_re and type_str and t_re.search(type_str)):
            return category, recommend, reason
    return "other", False, None


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
        category, recommend, reason = _classify(name, type_str)
        out.append({
            "name": name,
            "type": type_str,
            "resolvable": _is_resolvable(type_str) if type_str else False,
            "category": category,
            "recommended": recommend,
            "reason": reason,
            "large": bool(type_str and _LARGE_TYPE.search(type_str)),
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

        # A brand-new node must let DDS discovery settle before the graph is
        # visible: get_topic_names_and_types() returns [] until the node has been
        # spun long enough to receive other participants' announcements. The old
        # code broke out on the first non-empty read, which on a fresh node was
        # often still incomplete (or returned [] forever if it never spun enough).
        #
        # Spin actively for a settle window, and keep going until the count has
        # been STABLE across two reads (graph converged) or the window elapses. Use
        # wall-clock via spin iterations (node clock may be sim/zero on some setups).
        import time as _time

        deadline = _time.monotonic() + max(timeout_s, 2.0)
        names_and_types: list[tuple[str, list[str]]] = []
        prev_count = -1
        stable = 0
        while _time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            names_and_types = node.get_topic_names_and_types()
            count = len(names_and_types)
            # converged: same non-zero count seen twice in a row
            if count > 0 and count == prev_count:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            prev_count = count

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
