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
    """Render a single perspective-aisle frame.

    Layout:
      - Sky/ceiling fills the top third (the `bg_rgb` tint).
      - Floor is a trapezoid from the horizon to the bottom corners
        with receding horizontal stripes that scroll toward the viewer
        as `pose_x` grows — gives the impression of forward motion.
      - Left/right walls show evenly-spaced vertical "shelf rib" lines.
      - The planned path is drawn as a green line down the floor's
        centerline, and the robot indicator (orange dot) walks along it.

    Everything is still synthetic, but the perspective + scrolling
    floor reads as a real warehouse aisle at a glance.
    """
    img = Image.new("RGB", (FRAME_W, FRAME_H), bg_rgb)
    draw = ImageDraw.Draw(img)

    horizon_y = 80
    vp_x = FRAME_W // 2

    # ---- Floor trapezoid (filled) ----
    floor_color = (52, 54, 60)
    draw.polygon(
        [(0, FRAME_H), (FRAME_W, FRAME_H), (vp_x + 60, horizon_y), (vp_x - 60, horizon_y)],
        fill=floor_color,
    )

    # ---- Side walls (filled) ----
    wall_color = (38, 40, 48)
    draw.polygon(
        [(0, 0), (vp_x - 60, horizon_y), (0, FRAME_H)],
        fill=wall_color,
    )
    draw.polygon(
        [(FRAME_W, 0), (vp_x + 60, horizon_y), (FRAME_W, FRAME_H)],
        fill=wall_color,
    )

    # ---- Shelf ribs on the walls (evenly spaced verticals receding to vp) ----
    rib_color = (70, 72, 82)
    # World-space ribs every 1m, scrolling with pose_x. Use a small range
    # of "world distances" ahead of the robot.
    for d_world in [k - (pose_x % 1.0) for k in range(1, 12)]:
        if d_world <= 0.05:
            continue
        # 1/d gives a perspective scale; clamp so it stays in-frame.
        scale = 1.0 / d_world
        # Left wall: x at floor edge interpolates from 0 (close) toward vp_x-60 (far).
        far = 1.0 - min(1.0, scale * 0.3)
        x_left = int(0 + (vp_x - 60) * far)
        y_top = int(FRAME_H - (FRAME_H - horizon_y) * far)
        draw.line([(x_left, FRAME_H), (x_left, y_top)], fill=rib_color, width=1)
        # Mirror for right wall.
        x_right = FRAME_W - x_left
        draw.line([(x_right, FRAME_H), (x_right, y_top)], fill=rib_color, width=1)

    # ---- Floor stripes (perspective rows receding to vanishing point) ----
    stripe_color = (66, 68, 76)
    # Same world-space spacing as ribs so the floor lines up with them.
    for d_world in [k - (pose_x % 1.0) for k in range(1, 14)]:
        if d_world <= 0.05:
            continue
        far = 1.0 - min(1.0, 1.0 / d_world * 0.3)
        y = int(FRAME_H - (FRAME_H - horizon_y) * far)
        # Width tapers toward vanishing point.
        x_left = int(0 + (vp_x - 60) * far)
        x_right = FRAME_W - x_left
        draw.line([(x_left, y), (x_right, y)], fill=stripe_color, width=1)

    # ---- Horizon line (subtle, just for orientation) ----
    draw.line([(0, horizon_y), (FRAME_W, horizon_y)], fill=(90, 92, 102), width=1)

    # ---- Planned path: down the floor centerline ----
    plan_color = (90, 200, 110)
    draw.line([(vp_x, horizon_y + 2), (vp_x, FRAME_H - 4)], fill=plan_color, width=2)

    # ---- Robot indicator (orange dot) ----
    # Bottom of frame = "here" (where the robot is). Lateral pose_y
    # shifts it left/right so path deviation is visible.
    indicator_y = FRAME_H - 18
    indicator_x = vp_x + int(pose_y * 60)
    draw.ellipse(
        [indicator_x - 7, indicator_y - 7, indicator_x + 7, indicator_y + 7],
        fill=(255, 140, 0), outline=(20, 20, 20),
    )

    # ---- Top status bar (always on top) ----
    draw.rectangle([0, 0, FRAME_W, 22], fill=(0, 0, 0))
    text = f"{label}  t={t_s:5.2f}s  x={pose_x:5.2f}  y={pose_y:+.2f}"
    draw.text((6, 4), text, fill=(230, 230, 230))

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
