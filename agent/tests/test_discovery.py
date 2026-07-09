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
    result = disc.discover_topics()
    assert result["topics"] == []
    assert result["settled"] is True
    # No ROS = nothing more to wait for, but the env report still ships
    # (the UI shows it next to the isolation hint).
    assert "ros_env" in result


# --- settle_graph: the DDS settle loop, driven with a fake clock ------------
# Each spin() advances the fake clock 0.1s and steps through a scripted
# sequence of graph reads (the last read repeats once the script runs out).

def _run_settle(script, timeout_s=3.0, quiet_reads=5):
    t = [0.0]
    i = [0]

    def spin():
        t[0] += 0.1
        i[0] = min(i[0] + 1, len(script) - 1)

    def read():
        return script[i[0]]

    return disc.settle_graph(read, spin, timeout_s, quiet_reads, clock=lambda: t[0])


def test_settle_reports_unsettled_on_deadline():
    # A graph still churning when the window closes -> settled False, so the
    # caller knows the snapshot may be partial (the post-restart cold scan).
    a, b = ("/a", ["T"]), ("/b", ["T"])
    script = [[a], [b]] * 50   # alternates forever, never stable
    out, settled = _run_settle(script, timeout_s=1.0)
    assert settled is False


def test_settle_waits_past_an_early_plateau():
    # The old count-based check returned after seeing [a] twice (0.2s); the
    # graph then grew to 3 topics. settle_graph must ride out the plateau.
    a, b, c = ("/a", ["T"]), ("/b", ["T"]), ("/c", ["T"])
    script = [[], [a], [a], [a], [a, b], [a, b, c]] + [[a, b, c]] * 20
    out, settled = _run_settle(script)
    assert {n for n, _ in out} == {"/a", "/b", "/c"}
    assert settled is True


def test_settle_needs_the_set_stable_not_the_count():
    # Same count, different composition -> not converged yet.
    a, b, c = ("/a", ["T"]), ("/b", ["T"]), ("/c", ["T"])
    script = [[a, b], [a, c], [b, c]] + [[a, b, c]] * 20
    out, settled = _run_settle(script)
    assert {n for n, _ in out} == {"/a", "/b", "/c"}
    assert settled is True


def test_settle_breaks_early_once_stable():
    a = ("/a", ["T"])
    t = [0.0]

    def spin():
        t[0] += 0.1

    out, settled = disc.settle_graph(lambda: [a], spin, timeout_s=3.0,
                                     quiet_reads=5, clock=lambda: t[0])
    assert out == [a] and settled is True
    assert t[0] < 1.0   # stopped well before the window closed


def test_settle_empty_graph_times_out_empty():
    out, settled = _run_settle([[]] * 5, timeout_s=1.0)
    assert out == [] and settled is False


# --- publisher counts: telling a robot signal from our own echo -------------

def test_classify_includes_publisher_counts():
    out = classify_topics(
        [("/cmd_vel", ["geometry_msgs/msg/Twist"]), ("/scan", ["sensor_msgs/msg/LaserScan"])],
        publisher_counts={"/cmd_vel": 2, "/scan": 0},
    )
    by_name = {t["name"]: t for t in out}
    assert by_name["/cmd_vel"]["publishers"] == 2
    assert by_name["/scan"]["publishers"] == 0   # advertised (e.g. by our own
    # capture subscription) but nothing publishing -- the ghost-topic case


def test_classify_publisher_count_unknown_when_absent():
    out = classify_topics([("/cmd_vel", ["geometry_msgs/msg/Twist"])])
    assert out[0]["publishers"] is None


# --- persistent node plumbing ------------------------------------------------

def test_discover_topics_uses_a_persistent_node(monkeypatch):
    # A fake rclpy: discover_topics must create the node ONCE and reuse it on
    # the next call (the warm-cache fix for partial scans at scale).
    import sys
    import types

    created = []

    class FakeNode:
        def get_topic_names_and_types(self):
            return [("/a", ["std_msgs/msg/String"])]

        def count_publishers(self, name):
            return 1

    import time

    fake = types.ModuleType("rclpy")
    fake.ok = lambda: True
    fake.init = lambda: None
    fake.create_node = lambda name: created.append(name) or FakeNode()
    # sleep like the real spin_once, or the daemon spinner busy-loops and
    # burns CPU for the rest of the suite
    fake.spin_once = lambda node, timeout_sec=0.0: time.sleep(timeout_sec or 0.01)
    monkeypatch.setitem(sys.modules, "rclpy", fake)
    monkeypatch.setattr(disc, "_NODE", {"node": None, "warmed": False})

    out1 = disc.discover_topics(timeout_s=0.3)
    out2 = disc.discover_topics(timeout_s=0.3)
    assert created == ["missiondebug_discovery"]   # one node, reused
    assert [t["name"] for t in out1["topics"]] == ["/a"] \
        == [t["name"] for t in out2["topics"]]
    # a COLD node's first scan is never presented as settled (it can fake
    # convergence mid-DDS-discovery); once warmed, scans are trusted.
    assert out1["settled"] is False and out2["settled"] is True
    assert out1["topics"][0]["publishers"] == 1
