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


def settle_graph(read, spin, timeout_s: float, quiet_reads: int = 5,
                 clock=None) -> list:
    """Spin until the graph is STABLE, then return the last read.

    Stable = the SET of topic names is non-empty and unchanged for quiet_reads
    consecutive reads (~0.5s of continuous spinning at 0.1s per spin). DDS
    announcements arrive in bursts spaced longer than one read, so comparing
    only the COUNT across 2 reads (the old check) routinely declared a partial
    graph converged: a scan could see 1 topic, 0 topics, or all of them
    depending on timing. Comparing the name SET also catches one topic
    replacing another between reads. If the graph never stabilizes (or stays
    empty), returns whatever the last read saw when the window closed.

    read/spin/clock are injected so this is testable without rclpy.
    """
    import time as _time
    now = clock or _time.monotonic
    deadline = now() + timeout_s
    result: list = []
    prev: set | None = None
    stable = 0
    while now() < deadline:
        spin()
        result = read()
        names = {n for n, _ in result}
        if names and names == prev:
            stable += 1
            if stable >= quiet_reads:
                break
        else:
            stable = 0
        prev = names
    return result


def classify_topics(
    names_and_types: list[tuple[str, list[str]]],
    publisher_counts: dict[str, int] | None = None,
) -> list[dict]:
    """Pure: turn raw (name, [types]) graph output into the discovery dicts.

    Filters hidden topics, takes the first type, flags resolvable + recommended,
    and sorts by name. Separated from the ROS plumbing so it is unit-testable
    without a running graph.

    publisher_counts maps topic name -> number of publishers on the graph. A
    topic appears in the graph when ANYTHING holds an endpoint on it, including
    this agent's own capture subscriptions, so a configured topic whose real
    publisher died long ago still lists forever (a self-sustaining ghost). The
    count lets the UI tell "a robot signal" from "our own echo": publishers == 0
    means advertised-but-silent. None/missing = unknown (caller had no counts).
    """
    counts = publisher_counts or {}
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
            "publishers": counts.get(name),
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
        # spun long enough to receive other participants' announcements. The
        # graph trickles in over 1-2s, so require real stability (see
        # settle_graph) instead of breaking on the first plateau.
        names_and_types = settle_graph(
            read=node.get_topic_names_and_types,
            spin=lambda: rclpy.spin_once(node, timeout_sec=0.1),
            timeout_s=max(timeout_s, 3.0))

        # Publisher count per topic, so the UI can tell a live robot signal
        # from a topic that exists only because of subscriptions (typically our
        # own capture node's). The agent publishes no ROS topics itself, so a
        # plain count needs no self-exclusion. Best-effort per topic: a count
        # that fails stays None (unknown) rather than failing the scan.
        publisher_counts: dict[str, int] = {}
        for name, _types in names_and_types:
            try:
                publisher_counts[name] = node.count_publishers(name)
            except Exception:
                pass

        return classify_topics(names_and_types, publisher_counts)
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
