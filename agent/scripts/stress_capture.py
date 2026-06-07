"""Stress test: the agent capture path (RingBuffer) for a robot type.

Feeds high-rate synthetic messages across every topic in a config
concurrently — one thread per topic, the way ROS callbacks land — for 2×
the buffer window so eviction is exercised, then checks: no errors, the
snapshot is valid + time-sorted, and memory stayed under the config's
max_total_bytes. Pick the config via MD_STRESS_CONFIG.

  MD_STRESS_CONFIG=drone-config.yaml       python scripts/stress_capture.py
  MD_STRESS_CONFIG=manipulator-config.yaml python scripts/stress_capture.py
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from missiondebug_agent.config import AgentConfig
from missiondebug_agent.ring_buffer import BufferedMessage, RingBuffer

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


# (schema substring, (payload_bytes, rate_hz)) — first match wins, so the
# more-specific schema names come first. Realistic sizes + rates per ROS type.
_PROFILES: list[tuple[str, tuple[int, int]]] = [
    ("compressedimage", (40_000, 30)),
    ("jointtrajectorycontrollerstate", (800, 50)),
    ("jointtrajectory", (600, 20)),
    ("jointstate", (240, 125)),   # arm at 125 Hz
    ("imu", (320, 100)),
    ("laserscan", (2400, 10)),
    ("navsatfix", (200, 20)),
    ("twist", (48, 50)),
    ("batterystate", (120, 5)),
    ("path", (1600, 5)),
    ("tfmessage", (400, 50)),
    ("odometry", (720, 50)),
    ("posestamped", (120, 10)),
    ("float64", (16, 50)),
    ("float32", (16, 50)),
    ("bool", (8, 20)),
    ("diagnostic", (500, 5)),
    ("result", (400, 5)),
    ("feedback", (400, 5)),
]


def profile(topic_type: str) -> tuple[int, int]:
    """(payload_bytes, rate_hz) for a ROS type — first matching profile."""
    t = topic_type.lower()
    for key, val in _PROFILES:
        if key in t:
            return val
    return (200, 20)


def main() -> None:
    name = os.environ.get("MD_STRESS_CONFIG", "drone-config.yaml")
    cfg = AgentConfig.load(EXAMPLES / name)
    feed_seconds = cfg.buffer_seconds * 2          # 2 windows → eviction fires
    rb = RingBuffer(window_seconds=cfg.buffer_seconds, max_total_bytes=cfg.max_total_bytes)

    errors: list[str] = []
    appended = 0
    alock = threading.Lock()

    def feed(topic) -> None:
        nonlocal appended
        size, rate = profile(topic.type)
        eff_rate = max(1, rate // max(1, topic.rate_divisor))   # honor sub-sampling
        n = int(eff_rate * feed_seconds)
        period = max(1, int(1e9 / eff_rate))
        payload = bytes(size)
        try:
            for i in range(n):
                ts = i * period
                rb.append(BufferedMessage(timestamp_ns=ts, wall_ns=ts,
                                          topic=topic.name, payload=payload))
            with alock:
                appended += n
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    t0 = time.perf_counter()
    threads = [threading.Thread(target=feed, args=(t,)) for t in cfg.topics]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    snap = rb.snapshot()
    total = rb.total_bytes()
    cap = cfg.max_total_bytes
    sorted_ok = all(
        snap[i].timestamp_ns <= snap[i + 1].timestamp_ns for i in range(len(snap) - 1)
    )

    print(f"{name}: topics={len(cfg.topics)} "
          f"window={cfg.buffer_seconds:.0f}s fed={feed_seconds:.0f}s")
    print(f"  appended={appended}  buffered_after_eviction={len(snap)}  "
          f"mem={total / 1e6:.1f}MB / cap={(cap or 0) / 1e6:.0f}MB  "
          f"throughput={appended / wall:.0f} msg/s  wall={wall:.2f}s  errors={len(errors)}")

    assert not errors, f"capture errored: {errors[:2]}"
    assert sorted_ok, "snapshot not time-sorted"
    if cap is not None:
        assert total <= cap, f"over byte cap: {total} > {cap}"
    print("  PASS — capture held: no errors, snapshot sorted, memory under cap\n")


if __name__ == "__main__":
    main()
