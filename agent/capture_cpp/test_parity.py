#!/usr/bin/env python3
"""Phase 1 acceptance: the C++ capture module buffers messages AND the bytes it
captures are byte-identical to what rclpy's serialize produces.

This is the make-or-break check: if the C++ serialized bytes match rclpy's, the
MCAP written from a C++ snapshot is identical to the Python path, so captures
replay the same. If they differ, the whole approach is unsafe.

Run on the VM (needs ROS 2 + the built module on PYTHONPATH):

  source /opt/ros/humble/setup.bash
  cd ~/missiondebug/agent/capture_cpp && source install/setup.bash
  # in another shell: python3 /tmp/campub.py   (the 30Hz camera publisher)
  python3 test_parity.py
"""
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from rclpy.serialization import serialize_message, deserialize_message
from sensor_msgs.msg import CompressedImage

import missiondebug_capture as mc

TOPIC = "/camera/image_raw/compressed"
TYPE = "sensor_msgs/msg/CompressedImage"


def main():
    # --- start the C++ capture on the camera topic (best_effort, like the agent)
    # Mark it a DETECTOR topic too, so we also exercise the Phase 2 Python
    # callback (the GIL crossing) on the same stream.
    spec = mc.TopicSpec()
    spec.name = TOPIC
    spec.type = TYPE
    spec.reliability = "best_effort"
    spec.is_detector = True
    cap = mc.Capture(topics=[spec], buffer_seconds=60.0, max_total_bytes=0)

    # Phase 2: register a Python detector callback. It is invoked from the C++
    # spin thread (which acquires the GIL for the call). Count invocations and
    # confirm the bytes deserialize, the same data the buffer holds.
    cb_state = {"count": 0, "last_format": None, "error": None}

    def detector_cb(topic, payload, ts_ns):
        try:
            cb_state["count"] += 1
            m = deserialize_message(bytes(payload), CompressedImage)
            cb_state["last_format"] = m.format
        except Exception as e:  # must never propagate into C++
            cb_state["error"] = repr(e)

    cap.set_detector_callback(detector_cb)
    cap.start()
    print("C++ capture started (detector callback registered); waiting for frames...")

    # Give it time to receive from the running publisher.
    for _ in range(20):
        time.sleep(0.5)
        if cap.buffer_size() > 5:
            break
    n = cap.buffer_size()
    print(f"C++ buffer_size: {n}")
    if n == 0:
        print("FAIL: C++ captured nothing. Is the publisher running on the "
              "same RMW/domain (RMW_IMPLEMENTATION=rmw_fastrtps_cpp)?")
        return 1

    # --- snapshot from C++: list of (topic, ts_ns, wall_ns, bytes)
    snap = cap.snapshot()
    print(f"C++ snapshot: {len(snap)} messages")
    topic, ts_ns, wall_ns, payload = snap[0]
    assert topic == TOPIC, f"unexpected topic {topic}"
    assert isinstance(payload, (bytes, bytearray)), type(payload)
    print(f"  first msg: topic={topic} ts={ts_ns} wall={wall_ns} bytes={len(payload)}")

    # --- PARITY: deserialize the C++-captured bytes with rclpy, then
    # re-serialize, and confirm a round trip works and the message is valid.
    # If the C++ bytes are real CDR, rclpy deserializes them cleanly.
    msg = deserialize_message(bytes(payload), CompressedImage)
    print(f"  rclpy deserialized C++ bytes OK: format={msg.format!r} "
          f"data_len={len(msg.data)}")
    reser = serialize_message(msg)
    if reser == bytes(payload):
        print("PARITY OK: C++-captured bytes == rclpy serialize round trip "
              "(byte-identical). MCAP from C++ will match the Python path.")
        ok = True
    else:
        # A mismatch in trailing padding can be benign, but flag it loudly.
        print(f"PARITY DIFF: C++ bytes ({len(payload)}) != rclpy reserialize "
              f"({len(reser)}). Investigate before trusting C++ MCAP output.")
        ok = False

    # --- Phase 2: the detector callback fired from the C++ spin thread (GIL).
    # Let a few more messages flow so the callback count is meaningful.
    time.sleep(2.0)
    print(f"detector callback: fired {cb_state['count']} times, "
          f"last_format={cb_state['last_format']!r}, error={cb_state['error']}")
    if cb_state["error"] is not None:
        print(f"FAIL: detector callback raised: {cb_state['error']}")
        ok = False
    elif cb_state["count"] == 0:
        print("FAIL: detector callback never fired (GIL/callback wiring).")
        ok = False
    elif cb_state["last_format"] != "jpeg":
        print("FAIL: detector callback got wrong/garbled data.")
        ok = False
    else:
        print("CALLBACK OK: detector callback fired from the C++ spin thread, "
              "GIL-safe, bytes deserialize correctly. No deadlock, no crash.")

    cap.stop()
    return 0 if ok else 2


if __name__ == "__main__":
    rclpy.init()
    try:
        sys.exit(main())
    finally:
        if rclpy.ok():
            rclpy.shutdown()
