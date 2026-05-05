"""Extract minimal session metadata from an MCAP file.

We use mcap.reader to read the summary section. start_time and end_time
are nanoseconds (per the MCAP spec); we convert to unix ms for the DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mcap.reader import make_reader


@dataclass
class McapMeta:
    started_at_ms: int
    ended_at_ms: int
    duration_ms: int
    topics: list[str]
    size_bytes: int


def extract(path: Path) -> McapMeta:
    size = path.stat().st_size
    with open(path, "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        if summary is None:
            raise ValueError(f"MCAP file {path} has no summary section")

        topics = sorted({c.topic for c in summary.channels.values()})
        stats = summary.statistics
        if stats is None:
            raise ValueError(f"MCAP file {path} has no statistics")
        start_ns = stats.message_start_time
        end_ns = stats.message_end_time

    started_ms = start_ns // 1_000_000
    ended_ms = end_ns // 1_000_000
    return McapMeta(
        started_at_ms=started_ms,
        ended_at_ms=ended_ms,
        duration_ms=max(0, ended_ms - started_ms),
        topics=topics,
        size_bytes=size,
    )
