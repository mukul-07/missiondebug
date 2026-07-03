"""save_now must work on BOTH capture engines and trim stale topics.

Regression for the 0.7.9 field bug: save_now called ring.evict_stale()
unconditionally, but the C++ engine's adapter (CppCaptureAdapter) exposes
only snapshot()/__len__/start/stop, so EVERY save on the C++ path raised
AttributeError -> HTTP 500 ("capture failed: HTTP 500" on the device card,
while the buffer kept filling). The fix trims the SNAPSHOT (_trim_stale, a
pure function) so the behavior lives at the surface both engines share, and
only calls the ring-level evict where it exists.
"""

from __future__ import annotations

from pathlib import Path

from missiondebug_agent.config import AgentConfig, TopicConfig
from missiondebug_agent.http_api import _trim_stale, save_now
from missiondebug_agent.ring_buffer import BufferedMessage, RingBuffer

S = 1_000_000_000
WALL0 = 1_700_000_000_000_000_000


def _loader(_t: str) -> str:
    return "string data\n"


def _bm(ts_ns: int, topic: str) -> BufferedMessage:
    return BufferedMessage(
        timestamp_ns=ts_ns, wall_ns=WALL0 + ts_ns, topic=topic, payload=b"\x00" * 4
    )


def _config(tmp_path: Path, **topic_kw) -> AgentConfig:
    return AgentConfig(
        robot_id="robot-001",
        buffer_seconds=60.0,
        topics=[
            TopicConfig(name="/cmd_vel", type="geometry_msgs/msg/Twist"),
            TopicConfig(name="/tf", type="tf2_msgs/msg/TFMessage", **topic_kw),
        ],
        output_dir=str(tmp_path),
    )


class CppLikeRing:
    """The C++ adapter's exact surface: NO evict_stale. If save_now ever
    again requires a method beyond this, this test catches it before the
    field does."""

    def __init__(self, items: list[BufferedMessage]) -> None:
        self._items = items

    def __len__(self) -> int:
        return len(self._items)

    def snapshot(self) -> list[BufferedMessage]:
        return list(self._items)

    def start(self) -> None:  # pragma: no cover - interface parity only
        pass

    def stop(self) -> None:  # pragma: no cover - interface parity only
        pass


def test_save_now_works_without_evict_stale(tmp_path):
    """The 0.7.9 regression: a C++-path save must not 500."""
    ring = CppLikeRing([_bm(i * S, "/cmd_vel") for i in range(5)])
    resp = save_now(_config(tmp_path), ring, label="test", schema_loader=_loader)
    assert Path(resp.path).exists()
    assert resp.topics == ["/cmd_vel"]


def test_save_now_trims_stale_topic_on_cpp_path(tmp_path):
    """A silent /tf burst hours older than the live /cmd_vel data must not
    stretch the saved file (the phantom-span bug), on the engine that has no
    ring-level evict."""
    items = [_bm(i * S, "/tf") for i in range(3)]  # t=0..2s, then silent
    now = 3 * 3600 * S
    items += [_bm(now - 2 * S + i, "/cmd_vel") for i in range(3)]  # live, ~3h later
    ring = CppLikeRing(items)
    resp = save_now(_config(tmp_path), ring, label="test", schema_loader=_loader)
    assert resp.topics == ["/cmd_vel"]
    assert resp.duration_s < 60.0


def test_save_now_still_evicts_python_ring(tmp_path):
    """The Python ring keeps its memory-freeing evict: after a save, the
    stale topic's messages are gone from the ring itself, not just the file."""
    ring = RingBuffer(window_seconds=60.0)
    for i in range(3):
        ring.append(_bm(i * S, "/tf"))
    now = 3 * 3600 * S
    for i in range(3):
        ring.append(_bm(now - 2 * S + i, "/cmd_vel"))
    resp = save_now(_config(tmp_path), ring, label="test", schema_loader=_loader)
    assert resp.topics == ["/cmd_vel"]
    assert {m.topic for m in ring.snapshot()} == {"/cmd_vel"}


def test_trim_stale_respects_per_topic_ring_seconds(tmp_path):
    """A topic with its own ring_seconds trims to THAT window, not the global."""
    config = _config(tmp_path, ring_seconds=1.0)  # /tf keeps only 1s
    now = 100 * S
    snap = [
        _bm(now - 3 * S, "/tf"),       # older than /tf's 1s window
        _bm(now - 3 * S, "/cmd_vel"),  # within the global 60s window
        _bm(now, "/cmd_vel"),
    ]
    trimmed = _trim_stale(snap, config)
    assert {m.topic for m in trimmed} == {"/cmd_vel"}
    assert len(trimmed) == 2


def test_trim_stale_unconfigured_topic_uses_default_window(tmp_path):
    """A topic in the snapshot but not in config (shouldn't happen, but never
    crash) falls back to the global window."""
    config = _config(tmp_path)
    now = 100 * S
    snap = [_bm(now - 30 * S, "/rogue"), _bm(now, "/cmd_vel")]
    trimmed = _trim_stale(snap, config)
    assert len(trimmed) == 2  # both within 60s of newest


def test_trim_stale_empty_snapshot_is_noop(tmp_path):
    assert _trim_stale([], _config(tmp_path)) == []
