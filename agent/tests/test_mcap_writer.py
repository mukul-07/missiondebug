"""End-to-end test: ring buffer -> MCAP writer -> mcap reader round-trip.

We skip rclpy by injecting a fake schema loader; payloads are arbitrary
bytes, which is fine for verifying file structure and metadata.
"""

from pathlib import Path

from mcap.reader import make_reader

from missiondebug_agent.mcap_writer import write_session
from missiondebug_agent.ring_buffer import BufferedMessage


FAKE_SCHEMA = "# fake schema for tests\nstring data\n"


def _loader(_type_str: str) -> str:
    return FAKE_SCHEMA


def test_write_session_roundtrip(tmp_path: Path):
    items = []
    base_wall = 1_700_000_000_000_000_000  # ns
    for i in range(20):
        items.append(BufferedMessage(
            timestamp_ns=i * 100_000_000,
            wall_ns=base_wall + i * 100_000_000,
            topic="/cmd_vel" if i % 2 == 0 else "/tf",
            payload=b"\x00\x01\x02\x03",
        ))

    out = tmp_path / "robot-001_test.mcap"
    meta = write_session(
        items, out,
        robot_id="robot-001",
        topic_types={
            "/cmd_vel": "geometry_msgs/msg/Twist",
            "/tf": "tf2_msgs/msg/TFMessage",
        },
        label="manual",
        schema_loader=_loader,
    )

    assert out.exists()
    assert meta.size_bytes == out.stat().st_size
    assert meta.label == "manual"
    assert set(meta.topics) == {"/cmd_vel", "/tf"}

    # Verify the file is parseable.
    with open(out, "rb") as f:
        reader = make_reader(f)
        summary = reader.get_summary()
        assert summary is not None
        assert summary.statistics is not None
        assert summary.statistics.message_count == 20
        topics = {c.topic for c in summary.channels.values()}
        assert topics == {"/cmd_vel", "/tf"}


def test_empty_snapshot_raises(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        write_session(
            [], tmp_path / "x.mcap",
            robot_id="r",
            topic_types={},
            schema_loader=_loader,
        )
