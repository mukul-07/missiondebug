"""v2 P3.5.1: structured-summary generator (zero-LLM, agent-side).

Tests the pure function in isolation — no MCAP write, no HTTP, no hub.
The summarizer must be deterministic (Hard Rule 27): identical inputs
must always produce the identical string, so embeddings + similarity
search downstream stay stable across hub restarts and re-ingests.
"""

from __future__ import annotations

from missiondebug_agent.config import AgentConfig, HubConfig, TopicConfig
from missiondebug_agent.ring_buffer import BufferedMessage
from missiondebug_agent.summarizer import build_summary


def _config(robot_id: str = "robot-001", subsystem: str | None = None) -> AgentConfig:
    return AgentConfig(
        robot_id=robot_id,
        buffer_seconds=60.0,
        topics=[TopicConfig(name="/cmd_vel", type="geometry_msgs/msg/Twist")],
        output_dir="/tmp",
        hub=HubConfig(subsystem=subsystem),
    )


def _msg(topic: str, payload_size: int = 8, ts_ns: int = 0) -> BufferedMessage:
    return BufferedMessage(
        timestamp_ns=ts_ns,
        wall_ns=1_700_000_000_000_000_000 + ts_ns,
        topic=topic,
        payload=b"\x00" * payload_size,
    )


# ---- trigger rendering --------------------------------------------------


def test_summary_renders_anomaly_label_as_rule_name():
    snap = [_msg("/cmd_vel")]
    s = build_summary(
        snap, _config(),
        label="anomaly:stall",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert "Auto-triggered by rule 'stall'" in s
    assert "robot-001" in s


def test_summary_renders_manual_label():
    s = build_summary(
        [_msg("/cmd_vel")], _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert "Manual save" in s


def test_summary_renders_no_label_as_manual_save():
    s = build_summary(
        [_msg("/cmd_vel")], _config(),
        label=None,
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert "Manual save" in s


def test_summary_renders_custom_label_verbatim():
    s = build_summary(
        [_msg("/cmd_vel")], _config(),
        label="post-deploy-smoke",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert "post-deploy-smoke" in s


# ---- subsystem ---------------------------------------------------------


def test_summary_omits_subsystem_when_none():
    """Hard Rule 23: subsystem is optional and free-form. Standalone v1.5
    deployments don't configure a hub.subsystem; the summary must read
    cleanly in that case."""
    s = build_summary(
        [_msg("/cmd_vel")], _config(subsystem=None),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert "subsystem" not in s


def test_summary_includes_subsystem_when_set():
    s = build_summary(
        [_msg("/cmd_vel")], _config(subsystem="warehouse-1"),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert "warehouse-1" in s


# ---- topic stats -------------------------------------------------------


def test_summary_ranks_topics_by_message_count_desc():
    """Top topics should appear in count-descending order — exactly the
    order an engineer scanning the line wants."""
    snap = (
        [_msg("/camera", ts_ns=i) for i in range(100)] +
        [_msg("/tf", ts_ns=i) for i in range(50)] +
        [_msg("/cmd_vel", ts_ns=i) for i in range(10)]
    )
    s = build_summary(
        snap, _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=2048,
    )
    cam_pos = s.index("/camera")
    tf_pos = s.index("/tf")
    cmd_vel_pos = s.index("/cmd_vel")
    assert cam_pos < tf_pos < cmd_vel_pos
    assert "/camera (100 msgs)" in s


def test_summary_collapses_long_topic_lists():
    """Long configs (e.g. the 30-topic stress fixture) should not produce
    a wall of text. We enumerate the top few and roll the rest into a
    '+N more' suffix."""
    snap = [_msg(f"/topic_{i:02d}", ts_ns=i) for i in range(20)]
    s = build_summary(
        snap, _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=2048,
    )
    assert "+15 more" in s  # 20 topics - 5 shown = 15 hidden


def test_summary_handles_single_topic():
    """Singular grammar matters for human readability."""
    s = build_summary(
        [_msg("/cmd_vel", ts_ns=i) for i in range(3)], _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert "across 1 topic:" in s
    assert "across 1 topics:" not in s


# ---- size rendering ----------------------------------------------------


def test_summary_size_under_kb_renders_bytes():
    s = build_summary(
        [_msg("/cmd_vel")], _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=512,
    )
    assert "512 B" in s


def test_summary_size_kb_range():
    s = build_summary(
        [_msg("/cmd_vel")], _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=2 * 1024,
    )
    assert "2.0 KB" in s


def test_summary_size_mb_range():
    s = build_summary(
        [_msg("/cmd_vel")], _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=int(2.5 * 1024 * 1024),
    )
    assert "2.5 MB" in s


# ---- determinism (Hard Rule 27) ---------------------------------------


def test_summary_is_deterministic():
    """HR27: identical inputs must produce identical output. Embedding
    pipeline (v2 P3.5.2) and similarity search depend on this — if the
    summary changes between calls, the same session would land at
    different points in the embedding space across hub restarts."""
    snap = (
        [_msg("/a", ts_ns=i) for i in range(5)] +
        [_msg("/b", ts_ns=i) for i in range(5)]
    )
    cfg = _config(subsystem="ops")
    kw = dict(
        label="anomaly:stall",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=4096,
    )
    s1 = build_summary(snap, cfg, **kw)
    s2 = build_summary(snap, cfg, **kw)
    s3 = build_summary(list(reversed(snap)), cfg, **kw)
    assert s1 == s2 == s3


def test_summary_tie_break_is_alphabetical():
    """When two topics have identical message counts, sort by name —
    this is the HR27 invariant for equal-count ties."""
    snap = [_msg("/z", ts_ns=0), _msg("/a", ts_ns=1)]
    s = build_summary(
        snap, _config(),
        label="manual",
        duration_ns=60_000_000_000,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=1024,
    )
    assert s.index("/a") < s.index("/z")


def test_summary_empty_snapshot_does_not_crash():
    """An empty buffer shouldn't be reachable in practice (save_now 409s
    before reaching us), but the function should be total."""
    s = build_summary(
        [], _config(),
        label="manual",
        duration_ns=0,
        started_wall_ns=1_700_000_000_000_000_000,
        size_bytes=0,
    )
    assert "no topics" in s
    assert "0.0s" in s
