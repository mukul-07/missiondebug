#!/usr/bin/env python3
"""Generate a 30-topic synthetic MCAP fixture for UI stress testing.

Designed to exercise everything v2 P1.7 added:
  - TrackScalar (auto-discovered numeric leaves on "other" topics)
  - TrackJsonInspector (collapsible tree per topic)
  - Topic-list expander (compact form on session list, full on detail)
  - Filter rail summary lines

Uses the `mcap` Python library directly with hand-crafted ros2msg
schema text + CDR-encoded payloads. No ROS install required.

Output: fixtures/warehouse_robot_30_topics.mcap

Topic shape (deliberately mixed to simulate a real warehouse-AGV fleet):
   8 scalar diagnostics  (battery/motor/temperature/current)
   4 state machines      (string + bool fields, JSON inspector territory)
   6 control feedback    (int32 + float32 mix)
   4 navigation          (Path-like with poses[] arrays)
   4 sensors             (joint_states with arrays; lidar-status flags)
   2 custom HW topics    (nested objects for the inspector)
   2 control            (multiple cmd_vel-shaped topics for the dropdown)
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

from mcap.writer import Writer

OUT_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "warehouse_robot_30_topics.mcap"

ROBOT_ID = "stress-robot-01"
SESSION_DURATION_S = 30.0
START_WALL_NS = 1_700_000_000_000_000_000  # matches sample_drive vintage


# ---- CDR encoding helpers --------------------------------------------
#
# Common Data Representation (CDR) is what ROS 2 serialization uses on the
# wire. For our synthetic primitives, the encoding is:
#   - 4-byte header: encapsulation kind (00 01 = little-endian) + options
#   - Field values, each aligned to their natural boundary

CDR_HEADER = b"\x00\x01\x00\x00"  # little-endian, no options


def encode_float64(v: float) -> bytes:
    """std_msgs/msg/Float64 — 8-byte aligned float64."""
    return CDR_HEADER + struct.pack("<d", v)


def encode_float32(v: float) -> bytes:
    """std_msgs/msg/Float32 — 4-byte float32."""
    return CDR_HEADER + struct.pack("<f", v)


def encode_int32(v: int) -> bytes:
    """std_msgs/msg/Int32 — 4-byte int32."""
    return CDR_HEADER + struct.pack("<i", v)


def encode_bool(v: bool) -> bytes:
    """std_msgs/msg/Bool — 1-byte boolean."""
    return CDR_HEADER + struct.pack("<B", 1 if v else 0)


def encode_string(v: str) -> bytes:
    """std_msgs/msg/String — uint32 length (incl. NUL) + UTF-8 + NUL."""
    encoded = v.encode("utf-8") + b"\x00"
    # String length field is at offset 4 (after the 4-byte CDR header),
    # naturally aligned, then the bytes.
    return CDR_HEADER + struct.pack("<I", len(encoded)) + encoded


def encode_diagnostic(percentage: float, voltage: float, current: float, temp: float) -> bytes:
    """Synthetic 4-field diagnostic: BatteryState-shaped (percentage,
    voltage_v, current_a, temperature_c). All float32."""
    return CDR_HEADER + struct.pack("<ffff", percentage, voltage, current, temp)


def encode_joint_state(positions: list[float], velocities: list[float]) -> bytes:
    """Synthetic JointState: two float64 arrays (position[], velocity[]).
    Each array prefixed with uint32 length, 8-byte aligned."""
    # CDR alignment is fiddly — for these synthetic schemas we keep things
    # simple by always 8-byte-aligning floats explicitly.
    body = struct.pack("<I", len(positions))                # uint32 length
    body += b"\x00\x00\x00\x00"                              # pad to 8-byte alignment
    body += struct.pack(f"<{len(positions)}d", *positions)
    body += struct.pack("<I", len(velocities))
    body += b"\x00\x00\x00\x00"
    body += struct.pack(f"<{len(velocities)}d", *velocities)
    return CDR_HEADER + body


def encode_cmd_vel(linear_x: float, angular_z: float) -> bytes:
    """Minimal Twist-shaped: just the two fields we render. The
    rendering UI only reads linear.x; the rest is filler."""
    # struct: linear{x,y,z} angular{x,y,z} = 6 float64 = 48 bytes
    return CDR_HEADER + struct.pack("<dddddd",
                                     linear_x, 0.0, 0.0,
                                     0.0, 0.0, angular_z)


# ---- Schema text (ros2msg format) ------------------------------------

SCHEMA_FLOAT64 = "float64 data\n"
SCHEMA_FLOAT32 = "float32 data\n"
SCHEMA_INT32 = "int32 data\n"
SCHEMA_BOOL = "bool data\n"
SCHEMA_STRING = "string data\n"
SCHEMA_DIAGNOSTIC = (
    "float32 percentage\n"
    "float32 voltage\n"
    "float32 current\n"
    "float32 temperature\n"
)
SCHEMA_JOINT_STATE = (
    "float64[] position\n"
    "float64[] velocity\n"
)
SCHEMA_TWIST = (
    "Vector3 linear\n"
    "Vector3 angular\n"
    "================================================================================\n"
    "MSG: geometry_msgs/Vector3\n"
    "float64 x\n"
    "float64 y\n"
    "float64 z\n"
)


# ---- Topic catalogue --------------------------------------------------
#
# (topic_name, type_name, schema_text, encode_fn, value_fn(t_s) -> bytes, hz)
#
# `value_fn` is called with the simulated wall-clock seconds from session
# start and returns a CDR payload. `hz` is the publish rate.

TOPICS = [
    # ---- 8 scalar diagnostics (Float64/Float32) ----
    ("/diag/battery_voltage", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(24.6 - 0.04 * t), 1.0),
    ("/diag/battery_percentage", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(max(0, 1.0 - 0.012 * t)), 1.0),
    ("/diag/motor_current_a", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(8.5 + 1.5 * math.sin(t * 0.8)), 5.0),
    ("/diag/motor_temp_c", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(42.0 + 0.3 * t), 1.0),
    ("/diag/cpu_load", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(0.45 + 0.1 * math.sin(t * 0.5)), 2.0),
    ("/diag/ram_mb", "std_msgs/msg/Float64", SCHEMA_FLOAT64,
     lambda t: encode_float64(2400 + 50 * math.sin(t * 0.3)), 1.0),
    ("/diag/disk_free_gb", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(82.4 - 0.001 * t), 0.5),
    ("/diag/uptime_s", "std_msgs/msg/Float64", SCHEMA_FLOAT64,
     lambda t: encode_float64(86400 + t), 0.5),

    # ---- 4 state machines (String + Int) ----
    ("/state/robot_mode", "std_msgs/msg/String", SCHEMA_STRING,
     lambda t: encode_string("AUTONOMOUS" if t < 20 else "MANUAL"), 1.0),
    ("/state/fsm", "std_msgs/msg/String", SCHEMA_STRING,
     lambda t: encode_string(
         "IDLE" if t < 5 else "MOVING" if t < 18 else "DOCKING" if t < 25 else "IDLE"
     ), 2.0),
    ("/state/error_code", "std_msgs/msg/Int32", SCHEMA_INT32,
     lambda t: encode_int32(0 if t < 15 else 42 if t < 17 else 0), 1.0),
    ("/state/mission_id", "std_msgs/msg/Int32", SCHEMA_INT32,
     lambda t: encode_int32(int(1000 + t // 10)), 1.0),

    # ---- 6 control feedback (int32 + float32 mix) ----
    ("/ctrl/throttle_pct", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(0.6 + 0.3 * math.sin(t)), 10.0),
    ("/ctrl/brake_pct", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(0.0 if math.sin(t) > 0 else 0.4), 10.0),
    ("/ctrl/steering_angle_rad", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(0.2 * math.sin(t * 0.5)), 10.0),
    ("/ctrl/gear", "std_msgs/msg/Int32", SCHEMA_INT32,
     lambda t: encode_int32(2 if t < 10 else 3 if t < 25 else 1), 1.0),
    ("/ctrl/wheel_left_rpm", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(120 + 30 * math.sin(t * 1.2)), 5.0),
    ("/ctrl/wheel_right_rpm", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(120 + 30 * math.sin(t * 1.2 + 0.1)), 5.0),

    # ---- 4 navigation-ish (still single-leaf for now) ----
    ("/nav/distance_to_goal_m", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(max(0, 18.5 - 0.6 * t)), 2.0),
    ("/nav/heading_deg", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32((t * 12.0) % 360.0), 5.0),
    ("/nav/path_index", "std_msgs/msg/Int32", SCHEMA_INT32,
     lambda t: encode_int32(int(t * 1.5)), 2.0),
    ("/nav/lookahead_m", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(0.8 + 0.2 * math.sin(t * 0.3)), 5.0),

    # ---- 4 sensors ----
    ("/sensor/joint_states", "sensor_msgs/msg/JointState", SCHEMA_JOINT_STATE,
     lambda t: encode_joint_state(
         positions=[math.sin(t + i * 0.5) for i in range(6)],
         velocities=[math.cos(t + i * 0.5) for i in range(6)],
     ), 5.0),
    ("/sensor/lidar_safety_active", "std_msgs/msg/Bool", SCHEMA_BOOL,
     lambda t: encode_bool(t > 12 and t < 14), 2.0),
    ("/sensor/imu_accel_x", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(0.5 * math.sin(t * 4.0)), 20.0),
    ("/sensor/imu_gyro_z", "std_msgs/msg/Float32", SCHEMA_FLOAT32,
     lambda t: encode_float32(0.1 * math.sin(t * 3.0)), 20.0),

    # ---- 2 multi-field custom hardware (diagnostics-shaped) ----
    ("/hw/bms", "fleet_msgs/msg/BMSFeedback", SCHEMA_DIAGNOSTIC,
     lambda t: encode_diagnostic(
         percentage=max(0, 1.0 - 0.012 * t),
         voltage=24.6 - 0.04 * t,
         current=8.5 + 1.5 * math.sin(t * 0.8),
         temp=42.0 + 0.3 * t,
     ), 2.0),
    ("/hw/motor_status", "fleet_msgs/msg/MotorStatus", SCHEMA_DIAGNOSTIC,
     lambda t: encode_diagnostic(
         percentage=0.85,
         voltage=48.0,
         current=12.0 + 2 * math.sin(t * 0.5),
         temp=55.0 + 0.2 * t,
     ), 2.0),

    # ---- 2 cmd_vel-shaped (so the velocity dropdown has options) ----
    ("/cmd_vel", "geometry_msgs/msg/Twist", SCHEMA_TWIST,
     lambda t: encode_cmd_vel(linear_x=0.5 + 0.3 * math.sin(t), angular_z=0.0), 10.0),
    ("/dock/cmd_vel", "geometry_msgs/msg/Twist", SCHEMA_TWIST,
     lambda t: encode_cmd_vel(
         linear_x=0.1 if t > 20 else 0.0, angular_z=0.0 if t > 20 else 0.0
     ), 10.0),
]


def main() -> None:
    if len(TOPICS) != 30:
        print(f"expected 30 topics, got {len(TOPICS)}", file=sys.stderr)
        sys.exit(1)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OUT_PATH, "wb") as f:
        writer = Writer(f)
        writer.start()

        # Register schemas + channels. Each topic gets its own channel.
        channel_ids: dict[str, int] = {}
        for topic_name, type_name, schema_text, _encode, _hz in TOPICS:
            schema_id = writer.register_schema(
                name=type_name,
                encoding="ros2msg",
                data=schema_text.encode("utf-8"),
            )
            channel_id = writer.register_channel(
                topic=topic_name,
                message_encoding="cdr",
                schema_id=schema_id,
                metadata={},
            )
            channel_ids[topic_name] = channel_id

        # Build a flat schedule of (publish_time_ns, topic, payload) tuples
        # so MCAP messages are written in roughly time order.
        schedule: list[tuple[int, str, bytes]] = []
        for topic_name, _type, _schema, encode, hz in TOPICS:
            period_s = 1.0 / hz
            t = 0.0
            seq = 0
            while t < SESSION_DURATION_S:
                ts_ns = START_WALL_NS + int(t * 1e9)
                payload = encode(t)
                schedule.append((ts_ns, topic_name, payload))
                t += period_s
                seq += 1
        schedule.sort(key=lambda x: x[0])

        # Set session-level metadata so the backend tags this session
        # like real ingested ones.
        writer.add_metadata(
            "missiondebug",
            {
                "session_id": "warehouse_robot_30_topics",
                "robot_id": ROBOT_ID,
                "label": "fixture:stress-30",
                "duration_ms": str(int(SESSION_DURATION_S * 1000)),
            },
        )

        for ts_ns, topic_name, payload in schedule:
            writer.add_message(
                channel_id=channel_ids[topic_name],
                log_time=ts_ns,
                publish_time=ts_ns,
                data=payload,
                sequence=0,
            )

        writer.finish()

    print(f"Wrote {OUT_PATH}")
    print(f"  topics:   {len(TOPICS)}")
    print(f"  messages: {len(schedule)}")
    print(f"  duration: {SESSION_DURATION_S}s")
    print(f"  size:     {OUT_PATH.stat().st_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
