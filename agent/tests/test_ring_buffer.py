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
