# Plan: C++ capture module (pybind11) for the MissionDebug agent

## Goal

Cut the agent's capture-hot-path CPU by moving the per-message work (ROS 2
subscribe -> serialize/copy -> ring buffer) from Python into a C++ extension,
exposed to the existing Python agent as a pybind11 module. Validated by the
2026-06-21 spike: C++ 3.4% vs Python 7.4% under a 30Hz/300KB camera (~2x).
On an Orin that is roughly 50% -> 25%. The DDS receive cost is shared and does
not vanish, so ~2x is the fair ceiling, not 10x.

Christian's recommendation ("port the capture node to C++"); architecture is
the friend's framing (C++ hot path, Python frontend via CPython/pybind11).

## Non-negotiable constraints

1. **Direct-repo users must not break.** Today `apt install` (prebuilt .deb) or
   a source build "just works" (pure Python). The C++ module adds a compile
   step. So: ship a **Python fallback**. Prebuilt-.deb users get C++
   automatically; source-builders or odd platforms fall back to the existing
   Python `RosBridge`. Selected at runtime, never a hard failure.
2. **Same MCAP output.** Byte-for-byte identical captures. The C++ path must
   produce the same serialized payloads the Python path does.
3. **Detectors keep working.** stall/path-deviation/rules/compare read
   *deserialized* fields on a few low-rate topics. Those must still get typed
   data. (See "the detector problem" below.)
4. **Additive + reversible.** A feature flag / capability detection, not a
   rewrite. We can disable the C++ path and be exactly back to today.

## The boundary (where C++ stops and Python starts)

The seam is already clean in the code:
- **Hot, per-message, high-rate -> C++:** subscribe (rclcpp generic
  subscription, SerializedMessage, no deserialize), copy bytes, time-windowed
  per-topic ring buffer with the global byte cap. This is `ros_bridge.py` +
  `ring_buffer.py`.
- **Cold, rare, low-rate -> stays Python:** config load, the HTTP/UDS control
  API, detectors, MCAP writing (`write_session`), hub sync, S3. Unchanged.
- **The handoff:** on a capture, C++ returns a snapshot = list of
  `(topic, timestamp_ns, wall_ns, payload_bytes)`. That is exactly what
  `RingBuffer.snapshot()` returns today and what `write_session` consumes
  (confirmed: mcap_writer reads item.topic / .wall_ns / .payload). So the C++
  module is a drop-in replacement for `RosBridge` + `RingBuffer`, presenting the
  same `snapshot()` shape. MCAP writing does not change at all.

## The detector problem (the one real wrinkle)

Detectors need typed fields (stall reads /cmd_vel linear.x + /odom twist;
path-deviation reads /tf + /plan; rules/compare read arbitrary dot-paths).
Those topics are LOW-RATE, so deserializing them costs little. Options:

- **Option D1 (recommended): split by need, in C++.** For each topic, C++ knows
  if a detector is attached (Python passes the list at start). Detector topics:
  C++ also delivers them to a Python callback as the *raw bytes*, and Python
  deserializes ONLY those few low-rate messages (rclpy.deserialize_message) to
  feed the detectors. Buffer-only topics (camera, lidar, the high-rate ones):
  pure C++, never cross into Python per-message. This keeps the hot path fully
  in C++ and pays Python deserialize only on the cheap topics. The detector
  callback frequency is low (cmd_vel/odom ~10-50Hz, not 30Hz x 300KB), so the
  Python crossing there is affordable.
- Option D2: deserialize detector fields in C++ too (rclcpp typed sub for those
  topics). More C++ work, avoids the Python crossing entirely. Defer; D1 first.

So: high-rate buffer-only topics never touch Python (the win); low-rate
detector topics cross to Python as bytes for deserialize (cheap). Mirrors the
current typed/raw split, but now the raw side is genuinely C++.

## Module API (pybind11 surface)

Minimal, mirrors what Python already calls:

```
capture = missiondebug_capture.Capture(
    topics=[{name,type,reliability,queue_depth,rate_divisor,ring_seconds}],
    buffer_seconds, max_total_bytes,
    detector_topics=[...],            # topics to ALSO deliver to Python (D1)
)
capture.set_detector_callback(fn)     # fn(topic, bytes, ts_ns) for detector topics
capture.start()                       # spins the rclcpp node on its own thread
snap = capture.snapshot()             # list of (topic, ts_ns, wall_ns, bytes)
capture.buffer_size()                 # for /healthz
capture.stop()
```

Python's `main.py` constructs this instead of `RosBridge`+`RingBuffer` when the
C++ module imports successfully; else falls back to the Python pair. `save_now`
calls `capture.snapshot()` exactly where it calls `ring.snapshot()` now.

## Build + packaging

- **Build system:** the module needs rclcpp + pybind11 + the message
  serialization libs. Two viable routes:
  - ament_cmake package that builds the .so (consistent with ROS tooling), OR
  - a setup.py/scikit-build that compiles the extension at pip/deb build time.
  Pick ament_cmake (the spike already uses it; ROS-native; finds rclcpp cleanly).
- **Per-distro/arch:** the .so is compiled, so it is distro+arch+Python-version
  specific. The release CI already builds a 4-way matrix (ubuntu22.04/24.04 x
  amd64/arm64); add the C++ module build to each. The agent .deb then ships a
  prebuilt .so per target.
- **Fallback wiring:** `try: import missiondebug_capture except ImportError:
  use Python path`. A source build without the toolchain, or an unsupported
  platform, silently runs Python. Log which path is active at startup.

## The fallback contract (protects direct-repo users)

- Prebuilt .deb (the normal install): ships the matching .so -> C++ path.
- `pip install` / source build with toolchain: builds the .so -> C++ path.
- Source build without toolchain, or import fails: Python path. Works, just
  the old CPU. NEVER a crash; the agent must run either way.
- A config/env override to force Python (MD_CAPTURE=python) for debugging and
  for guaranteeing identical behavior when diagnosing.

## Phasing

**Phase 0 (DONE): spike.** Proved ~2x. This file's parent dir.

**Phase 1: the C++ module, buffer-only path only.** Build the pybind11 module
doing subscribe+buffer+snapshot for buffer-only topics; detector topics
temporarily stay on the existing Python RosBridge running ALONGSIDE (both
subscribe; dedupe not needed since they own different topics). Wire `save_now`
to merge both snapshots. Ship behind MD_CAPTURE=cpp (opt-in), Python default.
Verify identical MCAP vs pure-Python on the same recording.

**Phase 2: fold detector topics in (Option D1).** C++ owns ALL topics; detector
topics also fire the Python callback with bytes; retire the parallel Python
RosBridge. Now the hot path is fully C++. Re-measure CPU end to end.

**Phase 3: make C++ the default + fallback.** Flip default to C++ when the .so
imports; Python fallback on ImportError. Add the .so to the release build
matrix. Ship in an agent minor (0.6.0).

**Phase 4 (optional): D2.** Move detector field extraction into C++ to drop the
last Python per-message crossing. Only if profiling says it matters.

## Risks / open questions

- **pybind11 + rclcpp + GIL.** The rclcpp spin thread is C++ (no GIL). The
  Python detector callback (D1) must acquire the GIL per call -> fine at
  low rate, would be a bottleneck at high rate (so NEVER call Python for
  high-rate buffer-only topics; that is the whole design).
- **Message serialization parity.** C++ rcl serialized bytes must equal what
  rclpy's serialize_message produces, so the MCAP is byte-identical and replays.
  Verify early (Phase 1 acceptance) by diffing an MCAP from each path.
- **Build complexity per distro/Python version.** The .so is tied to the
  build host's Python + ROS. The release matrix already pins these; confirm
  the .deb's Python matches the target's.
- **Maintenance cost.** Two capture paths (C++ + Python fallback) to keep in
  sync. Mitigate: keep the Python path as the reference/fallback, minimal,
  and test both produce identical MCAP in CI.
- **Capability sandbox:** unaffected. The capability downloads whatever agent
  .deb we publish; if that .deb ships the .so for the robot's distro/arch, the
  sandbox runs the C++ path with no privilege needed (it is just a .so, not
  eBPF/root). Confirm the sandbox's Python version matches the .so.

## Acceptance (per phase, but the bar)

- Same MCAP bytes as the Python path on an identical recording (diff test).
- Measured CPU drop on the VM under the 30Hz/300KB camera (target ~2x on the
  hot path, the spike number).
- All existing agent tests pass; Python fallback proven by forcing
  MD_CAPTURE=python.
- Direct-repo source build without the toolchain still runs (Python fallback).
