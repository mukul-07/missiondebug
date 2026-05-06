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
