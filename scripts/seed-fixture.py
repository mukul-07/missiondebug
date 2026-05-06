#!/usr/bin/env python3
"""Generate a synthetic 'demo drive' MCAP at fixtures/sample_drive.mcap.

The drive is 30 seconds long and deliberately stages both anomaly types:

  0-8s:    cruising forward at 0.5 m/s, on the planned path
  8-14s:   STALL — velocity drops to 0 for 6s (triggers stall detector)
  14-22s:  recovering, but drifts y from 0 to 0.8m off the planned path
           (triggers path-deviation detector)
  22-30s:  back on path, normal cruise

Topics produced: /cmd_vel, /tf, /plan.

Must be run on a machine with ROS 2 sourced (rclpy is needed for CDR
serialization). Output is committed to git so fresh clones don't need ROS.

Usage:
    source /opt/ros/humble/setup.bash
    .venv/bin/python scripts/seed-fixture.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running without `pip install -e` by injecting the agent's src.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent" / "src"))

try:
    from rclpy.serialization import serialize_message
    from geometry_msgs.msg import PoseStamped, TransformStamped, Twist
    from nav_msgs.msg import Path as PathMsg
    from sensor_msgs.msg import CompressedImage
    from tf2_msgs.msg import TFMessage
except ImportError as e:
    print(
        "ERROR: This script requires ROS 2 (rclpy + msg packages).\n"
        "  Run:  source /opt/ros/humble/setup.bash\n"
        f"  Then: {sys.executable} {' '.join(sys.argv)}\n"
        f"Original import error: {e}",
        file=sys.stderr,
    )
    sys.exit(1)

# PIL is optional. If absent, we skip the camera streams and only ship
# /cmd_vel /tf /plan in the fixture.
try:
    import io
    from PIL import Image, ImageDraw  # type: ignore
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
    print(
        "Note: Pillow not installed; skipping fake camera frames.\n"
        "      Install with: pip install pillow  (in the agent venv)\n"
        "      Then re-run to include video tracks in the fixture.",
        file=sys.stderr,
    )

from missiondebug_agent.mcap_writer import write_session  # noqa: E402
from missiondebug_agent.ring_buffer import BufferedMessage  # noqa: E402


# ---------- fake camera frame generator ----------

# 320x180 keeps individual JPEGs ~3 KB each.
FRAME_W, FRAME_H = 320, 180
# x of 0..15m (the planned path) maps across the canvas.
PATH_MAX_X = 15.0


def make_frame_jpeg(label: str, bg_rgb, t_s: float, pose_x: float, pose_y: float) -> bytes:
    img = Image.new("RGB", (FRAME_W, FRAME_H), bg_rgb)
    draw = ImageDraw.Draw(img)

    # Top bar with text.
    draw.rectangle([0, 0, FRAME_W, 26], fill=(0, 0, 0))
    text = f"{label}  t={t_s:5.2f}s  x={pose_x:5.2f}  y={pose_y:+.2f}"
    draw.text((6, 5), text, fill=(255, 255, 255))

    # Horizon line.
    horizon_y = 110
    draw.line([(0, horizon_y), (FRAME_W, horizon_y)], fill=(80, 80, 100), width=1)

    # Planned path: thin straight line at the horizon's y level.
    plan_y = horizon_y + 30
    draw.line([(20, plan_y), (FRAME_W - 20, plan_y)], fill=(80, 130, 80), width=2)

    # Robot indicator: orange circle that walks along the path with x,
    # bumps off-line vertically with y. y=0 is on the line; y=+1 lifts it.
    cx = int(20 + min(1.0, pose_x / PATH_MAX_X) * (FRAME_W - 40))
    cy = plan_y + int(-pose_y * 30)
    draw.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(255, 140, 0), outline=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def append_camera_frame(
    items: list,
    topic: str,
    label: str,
    bg_rgb,
    t_s: float,
    ts_ns: int,
    wall_ns: int,
    pose_x: float,
    pose_y: float,
) -> None:
    img_msg = CompressedImage()
    img_msg.format = "jpeg"
    img_msg.data = make_frame_jpeg(label, bg_rgb, t_s, pose_x, pose_y)
    items.append(
        BufferedMessage(
            timestamp_ns=ts_ns,
            wall_ns=wall_ns,
            topic=topic,
            payload=serialize_message(img_msg),
        )
    )


def main() -> None:
    duration_s = 30.0
    rate_hz = 10
    n_steps = int(duration_s * rate_hz)
    base_wall = 1_700_000_000_000_000_000  # arbitrary epoch

    items: list[BufferedMessage] = []

    # ---- /plan: straight line in 'map' from x=0 to x=15 ----
    plan = PathMsg()
    plan.header.frame_id = "map"
    for x in [0.0, 5.0, 10.0, 15.0]:
        ps = PoseStamped()
        ps.header.frame_id = "map"
        ps.pose.position.x = float(x)
        ps.pose.orientation.w = 1.0
        plan.poses.append(ps)
    items.append(
        BufferedMessage(
            timestamp_ns=0,
            wall_ns=base_wall,
            topic="/plan",
            payload=serialize_message(plan),
        )
    )

    # ---- Drive loop ----
    pose_x = 0.0
    pose_y = 0.0
    for i in range(n_steps):
        t_s = i / rate_hz
        ts_ns = int(t_s * 1e9)
        wall_ns = base_wall + ts_ns

        if t_s < 8.0:
            lin_x = 0.5
            dy = 0.0
        elif t_s < 14.0:
            lin_x = 0.0  # stall
            dy = 0.0
        elif t_s < 22.0:
            lin_x = 0.4
            dy = 0.10  # drift sideways: 0.10 m/s for 8s -> 0.8m off path
        else:
            lin_x = 0.5
            # Recover toward y=0
            dy = -min(0.10, pose_y * rate_hz)

        pose_x += lin_x / rate_hz
        pose_y += dy / rate_hz

        twist = Twist()
        twist.linear.x = float(lin_x)
        items.append(
            BufferedMessage(
                timestamp_ns=ts_ns,
                wall_ns=wall_ns,
                topic="/cmd_vel",
                payload=serialize_message(twist),
            )
        )

        tf = TFMessage()
        tfs = TransformStamped()
        tfs.header.frame_id = "map"
        tfs.child_frame_id = "base_link"
        tfs.transform.translation.x = float(pose_x)
        tfs.transform.translation.y = float(pose_y)
        tfs.transform.rotation.w = 1.0
        tf.transforms.append(tfs)
        items.append(
            BufferedMessage(
                timestamp_ns=ts_ns,
                wall_ns=wall_ns,
                topic="/tf",
                payload=serialize_message(tf),
            )
        )

        # Camera frames at 5 fps (every other 10 Hz tick).
        if HAVE_PIL and i % 2 == 0:
            append_camera_frame(
                items, "/front_camera/image_raw/compressed", "FRONT CAM",
                (35, 45, 65), t_s, ts_ns, wall_ns, pose_x, pose_y,
            )
            append_camera_frame(
                items, "/rear_camera/image_raw/compressed", "REAR CAM",
                (60, 40, 40), t_s, ts_ns, wall_ns, pose_x, pose_y,
            )

    topic_types = {
        "/cmd_vel": "geometry_msgs/msg/Twist",
        "/tf": "tf2_msgs/msg/TFMessage",
        "/plan": "nav_msgs/msg/Path",
    }
    if HAVE_PIL:
        topic_types["/front_camera/image_raw/compressed"] = "sensor_msgs/msg/CompressedImage"
        topic_types["/rear_camera/image_raw/compressed"] = "sensor_msgs/msg/CompressedImage"

    out = ROOT / "fixtures" / "sample_drive.mcap"
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = write_session(
        items,
        out,
        robot_id="fixture-robot",
        topic_types=topic_types,
        label="fixture:demo-drive",
    )
    print(
        f"Wrote {out.relative_to(ROOT)}: "
        f"{meta.duration_ns / 1e9:.2f}s, {meta.size_bytes} bytes, "
        f"{len(items)} messages"
    )
    print("Commit it:")
    print(f"  git add fixtures/sample_drive.mcap fixtures/")
    print('  git commit -m "Add demo-drive fixture for onboarding"')


if __name__ == "__main__":
    main()
