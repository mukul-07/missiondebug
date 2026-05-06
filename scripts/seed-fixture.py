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

from missiondebug_agent.mcap_writer import write_session  # noqa: E402
from missiondebug_agent.ring_buffer import BufferedMessage  # noqa: E402


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

    out = ROOT / "fixtures" / "sample_drive.mcap"
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = write_session(
        items,
        out,
        robot_id="fixture-robot",
        topic_types={
            "/cmd_vel": "geometry_msgs/msg/Twist",
            "/tf": "tf2_msgs/msg/TFMessage",
            "/plan": "nav_msgs/msg/Path",
        },
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
