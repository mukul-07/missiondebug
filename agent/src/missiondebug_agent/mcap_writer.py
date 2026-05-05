"""MCAP writer: takes a ring buffer snapshot and writes a valid MCAP file.

Uses mcap-ros2-support to register schemas for known ROS 2 types. Schemas
are loaded via rosidl_runtime_py if available, with a fallback that lets
unit tests inject a synthetic schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mcap.writer import Writer
from mcap.well_known import SchemaEncoding

from .ring_buffer import BufferedMessage

log = logging.getLogger(__name__)


@dataclass
class SessionMetadata:
    session_id: str
    robot_id: str
    started_wall_ns: int
    ended_wall_ns: int
    duration_ns: int
    topics: list[str]
    label: str | None
    path: str
    size_bytes: int


def _topic_type_map(topics_config) -> dict[str, str]:
    return {t.name: t.type for t in topics_config}


_PRIMITIVES = {
    "bool", "byte", "char",
    "int8", "uint8", "int16", "uint16", "int32", "uint32", "int64", "uint64",
    "float32", "float64",
    "string", "wstring",
}


def _msg_path(type_str: str) -> Path:
    """type_str like 'sensor_msgs/msg/CompressedImage' -> path to .msg."""
    try:
        from rosidl_runtime_py import get_interface_path  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "rosidl_runtime_py not available — ROS 2 environment must be sourced"
        ) from e
    return Path(get_interface_path(type_str))


def _load_ros2_schema_text(type_str: str) -> str:
    """Concatenate the .msg text for a type and ALL transitively referenced
    types, using the standard ROS multi-message schema format expected by
    @foxglove/rosmsg and mcap_ros2:

        <root .msg body>
        ====...====
        MSG: pkg/SubType
        <sub .msg body>
        ====...====
        MSG: pkg/AnotherType
        ...

    This is required so the browser-side parser can deserialize messages
    that nest other message types (Twist -> Vector3, TFMessage -> Transform,
    etc.). Loading just the root .msg leaves the parser without enough info
    and decoding silently produces empty output.
    """
    visited: dict[str, str] = {}

    def normalize(t: str) -> str:
        # 'pkg/Type' -> 'pkg/msg/Type' so get_interface_path resolves.
        if t.count("/") == 1:
            pkg, name = t.split("/")
            return f"{pkg}/msg/{name}"
        return t

    def visit(t: str) -> None:
        t = normalize(t)
        if t in visited:
            return
        visited[t] = ""  # placeholder to break cycles
        try:
            text = _msg_path(t).read_text()
        except Exception as e:
            log.warning("Could not load schema for %s: %s", t, e)
            return
        visited[t] = text

        pkg_of_t = t.split("/")[0]
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            tokens = line.split()
            if len(tokens) < 2:
                continue
            # Constant declaration: 'TYPE NAME = value'
            if "=" in raw_line.split("#", 1)[0]:
                continue
            field_type = tokens[0]
            # Strip array bound: 'Type[]', 'Type[N]', 'string<=10', etc.
            field_type = field_type.split("[", 1)[0].split("<", 1)[0]
            if field_type in _PRIMITIVES:
                continue
            if "/" in field_type:
                visit(field_type)
            else:
                # Same-package implicit reference.
                visit(f"{pkg_of_t}/msg/{field_type}")

    visit(type_str)

    root_key = normalize(type_str)
    root_text = visited.pop(root_key, "")
    parts = [root_text]
    sep = "=" * 80
    for tname, text in visited.items():
        if not text:
            continue
        short = tname.replace("/msg/", "/")  # MSG: pkg/Type form
        parts.append(f"{sep}\nMSG: {short}\n{text}")
    return "\n".join(parts)


def write_session(
    snapshot: Iterable[BufferedMessage],
    output_path: Path,
    *,
    robot_id: str,
    topic_types: dict[str, str],
    label: str | None = None,
    schema_loader=_load_ros2_schema_text,
) -> SessionMetadata:
    """Write a session MCAP file. Returns its metadata.

    Messages are written in monotonic-timestamp order. log_time and
    publish_time are both set to the wall-clock ns recorded at append.
    """
    items = sorted(snapshot, key=lambda m: m.timestamp_ns)
    if not items:
        raise ValueError("Cannot write an empty session")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    started_wall = items[0].wall_ns
    ended_wall = items[-1].wall_ns
    topics_seen: set[str] = set()

    with open(output_path, "wb") as f:
        writer = Writer(f)
        writer.start(profile="ros2", library="missiondebug-agent")

        # File-level metadata so the backend can identify the robot
        # without parsing the filename.
        writer.add_metadata(
            name="missiondebug",
            data={
                "robot_id": robot_id,
                "label": label or "",
            },
        )

        # Register one schema + channel per topic encountered.
        channel_for: dict[str, int] = {}
        for item in items:
            if item.topic in channel_for:
                continue
            type_str = topic_types.get(item.topic)
            if type_str is None:
                log.warning("Topic %s not in config; skipping", item.topic)
                continue
            schema_text = schema_loader(type_str)
            schema_id = writer.register_schema(
                name=type_str,
                encoding=SchemaEncoding.ROS2,
                data=schema_text.encode(),
            )
            channel_id = writer.register_channel(
                schema_id=schema_id,
                topic=item.topic,
                message_encoding="cdr",
            )
            channel_for[item.topic] = channel_id

        for item in items:
            channel_id = channel_for.get(item.topic)
            if channel_id is None:
                continue
            topics_seen.add(item.topic)
            writer.add_message(
                channel_id=channel_id,
                log_time=item.wall_ns,
                publish_time=item.wall_ns,
                data=item.payload,
                sequence=0,
            )

        writer.finish()

    size_bytes = output_path.stat().st_size
    duration_ns = ended_wall - started_wall

    return SessionMetadata(
        session_id=output_path.stem,
        robot_id=robot_id,
        started_wall_ns=started_wall,
        ended_wall_ns=ended_wall,
        duration_ns=duration_ns,
        topics=sorted(topics_seen),
        label=label,
        path=str(output_path),
        size_bytes=size_bytes,
    )
