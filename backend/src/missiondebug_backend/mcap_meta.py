"""Extract minimal session metadata from an MCAP file.

Reads the summary section for time/topic info, then iterates metadata
records to find MissionDebug's `missiondebug` block (robot_id, label).
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
    robot_id: str | None
    label: str | None


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

        robot_id: str | None = None
        label: str | None = None
        try:
            for record in reader.iter_metadata():
                if record.name == "missiondebug":
                    robot_id = record.metadata.get("robot_id") or robot_id
                    lab = record.metadata.get("label")
                    if lab:
                        label = lab
        except Exception:
            # Older files written without metadata records — fine, fall back to filename.
            pass

    started_ms = start_ns // 1_000_000
    ended_ms = end_ns // 1_000_000
    return McapMeta(
        started_at_ms=started_ms,
        ended_at_ms=ended_ms,
        duration_ms=max(0, ended_ms - started_ms),
        topics=topics,
        size_bytes=size,
        robot_id=robot_id,
        label=label,
    )
