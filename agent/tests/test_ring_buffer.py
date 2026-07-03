"""Tests for the ring buffer: append, eviction, snapshot, thread safety."""

from __future__ import annotations

import threading
import time

from missiondebug_agent.ring_buffer import BufferedMessage, RingBuffer


def _msg(ts_ns: int, topic: str = "/t", payload: bytes = b"x") -> BufferedMessage:
    return BufferedMessage(timestamp_ns=ts_ns, wall_ns=ts_ns, topic=topic, payload=payload)


def test_append_and_len():
    rb = RingBuffer(window_seconds=1.0)
    for i in range(5):
        rb.append(_msg(i))
    assert len(rb) == 5


def test_eviction_drops_old_entries():
    rb = RingBuffer(window_seconds=1.0)  # 1e9 ns window
    rb.append(_msg(0))
    rb.append(_msg(int(0.5e9)))
    rb.append(_msg(int(2e9)))  # evicts the first two
    snap = rb.snapshot()
    assert len(snap) == 1
    assert snap[0].timestamp_ns == int(2e9)


def test_eviction_keeps_boundary_entries():
    rb = RingBuffer(window_seconds=1.0)
    rb.append(_msg(0))
    rb.append(_msg(int(1e9)))  # exactly at boundary; cutoff = 0, 0 not < 0
    snap = rb.snapshot()
    assert len(snap) == 2


def test_snapshot_is_independent_copy():
    rb = RingBuffer(window_seconds=10.0)
    rb.append(_msg(1))
    snap = rb.snapshot()
    rb.append(_msg(2))
    assert len(snap) == 1
    assert len(rb) == 2


def test_clear():
    rb = RingBuffer(window_seconds=10.0)
    for i in range(3):
        rb.append(_msg(i))
    rb.clear()
    assert len(rb) == 0


def test_thread_safety_concurrent_producers():
    rb = RingBuffer(window_seconds=60.0)
    n_threads = 8
    n_per_thread = 1000
    barrier = threading.Barrier(n_threads)

    def producer(tid: int):
        barrier.wait()
        base = time.monotonic_ns() + tid * 10_000_000
        for i in range(n_per_thread):
            rb.append(_msg(base + i, topic=f"/t{tid}"))

    threads = [threading.Thread(target=producer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Window is wider than the spread, so all should remain.
    assert len(rb) == n_threads * n_per_thread


def test_invalid_window():
    import pytest

    with pytest.raises(ValueError):
        RingBuffer(window_seconds=0)


# ---------- v1.5 additions ----------


def test_per_topic_windows_dont_evict_each_other():
    """A burst on one topic must not affect another topic's history."""
    rb = RingBuffer(window_seconds=10.0)
    rb.configure_topic("/cam", window_seconds=0.5)   # short window
    rb.configure_topic("/state", window_seconds=10.0)  # long window

    # State message at t=0
    rb.append(_msg(0, topic="/state"))
    # Camera burst over 1s — past /cam's 0.5s window
    for i in range(20):
        rb.append(_msg(int(i * 0.05e9), topic="/cam"))
    # Camera should retain only the last ~0.5s of frames
    assert rb.topic_size("/cam") <= 11
    # State should still hold its single message
    assert rb.topic_size("/state") == 1
    assert any(m.topic == "/state" and m.timestamp_ns == 0 for m in rb.snapshot())


def test_global_byte_budget_drops_oldest_across_topics():
    rb = RingBuffer(window_seconds=10.0, max_total_bytes=30)
    # 5 byte payloads, 4 topics — total grows past 30 bytes.
    rb.append(_msg(0, topic="/a", payload=b"00000"))   # 5 bytes
    rb.append(_msg(1, topic="/b", payload=b"11111"))   # 10
    rb.append(_msg(2, topic="/c", payload=b"22222"))   # 15
    rb.append(_msg(3, topic="/d", payload=b"33333"))   # 20
    rb.append(_msg(4, topic="/a", payload=b"44444"))   # 25
    rb.append(_msg(5, topic="/b", payload=b"55555"))   # 30
    rb.append(_msg(6, topic="/c", payload=b"66666"))   # 35 -> evict oldest
    assert rb.total_bytes() <= 30
    snap = rb.snapshot()
    assert all(m.timestamp_ns >= 1 for m in snap)  # ts=0 dropped first


def test_global_budget_off_by_default():
    rb = RingBuffer(window_seconds=10.0)  # max_total_bytes=None
    for i in range(1000):
        rb.append(_msg(i, payload=b"x" * 100))
    assert rb.total_bytes() == 100_000


def test_topic_size_unknown_returns_zero():
    rb = RingBuffer(window_seconds=10.0)
    assert rb.topic_size("/never-seen") == 0


def test_configure_topic_after_append():
    """Reconfiguring an already-active topic shrinks its window."""
    rb = RingBuffer(window_seconds=10.0)
    for i in range(5):
        rb.append(_msg(int(i * 0.5e9), topic="/x"))  # spans 0..2s
    # Shrink window — next append triggers eviction with new window.
    rb.configure_topic("/x", window_seconds=0.5)
    rb.append(_msg(int(2.5e9), topic="/x"))
    # Only entries within 0.5s of t=2.5s should remain.
    snap = rb.snapshot()
    assert all(m.timestamp_ns >= int(2e9) for m in snap)


def test_invalid_topic_window():
    import pytest

    rb = RingBuffer(window_seconds=10.0)
    with pytest.raises(ValueError):
        rb.configure_topic("/x", window_seconds=0)


def test_evict_stale_drops_silent_topics_at_save():
    # The bug: a topic that stops publishing keeps its last window forever
    # (append-time eviction only fires on new messages). At save the buffer
    # spans the gap between the stale topic and the live one.
    rb = RingBuffer(window_seconds=90.0)   # 90e9 ns window
    S = 1_000_000_000
    # /tf published a burst at t=0..1s, then went silent
    for i in range(3):
        rb.append(_msg(i * S, topic="/tf"))
    # /cmd_vel is live now, ~3 hours later
    now = 3 * 3600 * S
    for i in range(3):
        rb.append(_msg(now - 2 * S + i, topic="/cmd_vel"))
    # before eviction the snapshot spans ~3 hours (the bug)
    snap = rb.snapshot()
    span_before = snap[-1].timestamp_ns - snap[0].timestamp_ns
    assert span_before > 90 * S
    # evict_stale as of now: the silent /tf (all older than now-90s) is dropped
    rb.evict_stale(now)
    snap = rb.snapshot()
    assert {m.topic for m in snap} == {"/cmd_vel"}
    assert snap[-1].timestamp_ns - snap[0].timestamp_ns < 90 * S


def test_evict_stale_keeps_recent_messages():
    rb = RingBuffer(window_seconds=10.0)
    S = 1_000_000_000
    now = 100 * S
    # messages arrive in timestamp order (as real ROS messages do)
    rb.append(_msg(now - 20 * S, topic="/a"))  # older than the window at save
    rb.append(_msg(now - 5 * S, topic="/a"))   # within the 10s window
    rb.evict_stale(now)
    snap = rb.snapshot()
    assert len(snap) == 1 and snap[0].timestamp_ns == now - 5 * S


def test_evict_stale_honors_per_topic_window():
    rb = RingBuffer(window_seconds=10.0)
    rb.configure_topic("/cam", window_seconds=1.0)
    S = 1_000_000_000
    now = 100 * S
    rb.append(_msg(now - 3 * S, topic="/cam"))    # older than cam's 1s window
    rb.append(_msg(now - 3 * S, topic="/state"))  # within state's 10s window
    rb.evict_stale(now)
    assert {m.topic for m in rb.snapshot()} == {"/state"}


def test_evict_stale_default_reference_is_newest_message():
    # No now_ns: trim relative to the freshest captured message. The silent
    # /tf burst (hours old) is dropped; the live /cmd_vel window is kept.
    rb = RingBuffer(window_seconds=90.0)
    S = 1_000_000_000
    for i in range(3):
        rb.append(_msg(i * S, topic="/tf"))          # t=0..2s, then silent
    for i in range(3):
        rb.append(_msg(3 * 3600 * S + i, topic="/cmd_vel"))  # ~3h later, live
    rb.evict_stale()   # reference = newest = the /cmd_vel burst
    assert {m.topic for m in rb.snapshot()} == {"/cmd_vel"}


def test_evict_stale_all_stale_keeps_last_window():
    # If EVERY topic is stale, the data-relative default keeps each topic's
    # last real window rather than emptying the buffer.
    rb = RingBuffer(window_seconds=10.0)
    S = 1_000_000_000
    for i in range(5):
        rb.append(_msg(i * S, topic="/a"))   # 0..4s, all "old" in wall time
    rb.evict_stale()   # newest = 4s; window 10s -> keeps everything
    assert len(rb.snapshot()) == 5


def test_evict_stale_empty_buffer_is_noop():
    rb = RingBuffer(window_seconds=10.0)
    rb.evict_stale()   # must not raise
    assert len(rb.snapshot()) == 0
