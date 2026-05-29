"""Structured session summary — agent-side, deterministic, zero-LLM.

Generated at save time from the ring buffer snapshot and config. The
output is a short human-readable string describing the trigger, robot,
and topic activity. Stored on the hub's session row and used by:

- Session list / detail UI (so engineers see what each capture is about
  without opening it)
- The embedding pipeline (v2 P3.5.2) for similarity search
- Fleet incident dashboard rollups (v2 P3.5.5)

v1 content is metadata-only: rule name, robot, subsystem, duration,
per-topic message counts, total payload size. Rich telemetry deltas
("/cmd_vel.linear.x dropped 1.2 → 0.0 m/s") require message decoding,
which lives in a later sub-phase — for v1 we ship what the agent
already has in hand at save time.

Hard Rule 27: summaries are deterministic and immutable once written.
Hard Rule 24: structured summaries work fully offline — no LLM, no
network call, no API key required.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .config import AgentConfig
from .ring_buffer import BufferedMessage


# How many topics to enumerate inline before collapsing the tail into a
# "+ N more". Five fits the common-case fleet config (4-8 topics) and keeps
# the 30-topic stress fixture from producing a wall of text.
_TOP_TOPICS = 5


def _format_trigger(label: str | None) -> str:
    """Render the trigger source in human-readable form.

    The agent's label convention (set in main.py):
      - "anomaly:<rule_name>" for auto-triggers (stall, path-deviation,
        custom YAML rules)
      - "manual" or None for HTTP-initiated saves
    """
    if not label:
        return "Manual save"
    if label.startswith("anomaly:"):
        rule = label[len("anomaly:"):] or "unknown"
        return f"Auto-triggered by rule '{rule}'"
    if label == "manual":
        return "Manual save"
    return f"Triggered by '{label}'"


def _format_topic_stats(snap: list[BufferedMessage]) -> str:
    """Top topics by message count in a compact one-liner.

    Sort is deterministic — count descending, ties broken alphabetically
    so HR27 (immutable summary for identical snapshots) holds even when
    two topics have the same count.
    """
    if not snap:
        return "no topics"
    counts = Counter(m.topic for m in snap)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    head = ranked[:_TOP_TOPICS]
    tail_count = len(ranked) - len(head)
    rendered = ", ".join(f"{topic} ({n} msgs)" for topic, n in head)
    if tail_count > 0:
        rendered += f", +{tail_count} more"
    return rendered


def _format_size(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count / (1024 * 1024):.1f} MB"


def build_summary(
    snap: list[BufferedMessage],
    config: AgentConfig,
    *,
    label: str | None,
    duration_ns: int,
    started_wall_ns: int,
    size_bytes: int,
) -> str:
    """Build a thin structured summary string for the session.

    Pure function — same inputs always produce the same output. Safe to
    call from the save_now hot path; runs in well under 1ms for typical
    snapshot sizes (no message decoding, only metadata aggregation).

    `size_bytes` is the on-disk MCAP file size, which is larger than the
    sum of raw payloads in `snap` (MCAP framing, schemas, indexes). We
    use the file size because that's what the operator sees in the
    session list and what billing rolls up against.
    """
    trigger = _format_trigger(label)
    started = datetime.fromtimestamp(started_wall_ns / 1e9, tz=timezone.utc)
    started_str = started.strftime("%Y-%m-%d %H:%M:%S UTC")
    duration_s = duration_ns / 1e9

    subsystem = config.hub.subsystem
    where = f"on {config.robot_id}"
    if subsystem:
        where += f" (subsystem: {subsystem})"

    topic_part = _format_topic_stats(snap)
    size_part = _format_size(size_bytes)
    unique_topics = len({m.topic for m in snap})

    return (
        f"{trigger} at {started_str} {where}. "
        f"Captured {duration_s:.1f}s across {unique_topics} "
        f"topic{'s' if unique_topics != 1 else ''}: "
        f"{topic_part}. Total payload: {size_part}."
    )
