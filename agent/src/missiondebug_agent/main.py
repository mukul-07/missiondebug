"""Agent entry point. Wires config -> ring buffer -> ROS bridge ->
anomaly detector -> HTTP API, runs everything until SIGINT.
"""

from __future__ import annotations

import argparse
import logging
import threading
from pathlib import Path

import uvicorn

from .anomaly import StallAnomaly, StallDetector
from .config import AgentConfig
from .http_api import build_app, save_now
from .ring_buffer import RingBuffer

log = logging.getLogger("missiondebug_agent")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.example.yaml")
    args = parser.parse_args()

    config = AgentConfig.load(args.config)
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)

    ring = RingBuffer(window_seconds=config.buffer_seconds)
    app = build_app(config, ring)

    def trigger_save_on_stall(_anomaly: StallAnomaly) -> None:
        log.info("Auto-saving session due to stall anomaly")
        try:
            resp = save_now(config, ring, label="anomaly:stall")
            log.info("Auto-saved %s (%.2fs)", resp.session_id, resp.duration_s)
        except Exception:
            log.exception("Auto-save failed")

    detector = StallDetector(
        velocity_threshold=config.anomaly.stall_velocity_threshold,
        duration_seconds=config.anomaly.stall_duration_seconds,
        cooldown_seconds=config.anomaly.cooldown_seconds,
        on_stall=trigger_save_on_stall,
    )

    def on_cmd_vel(msg, ts_ns: int) -> None:
        # geometry_msgs/msg/Twist
        try:
            lin = float(msg.linear.x)
            ang = float(msg.angular.z)
        except AttributeError:
            log.exception("Unexpected /cmd_vel payload shape")
            raise
        detector.update(lin, ang, ts_ns)

    # Import deferred so unit tests don't need rclpy.
    from .ros_bridge import RosBridge

    bridge = RosBridge(config, ring, cmd_vel_callback=on_cmd_vel)

    # Run rclpy spin in a background thread; uvicorn serves HTTP on the main thread.
    spin_thread = threading.Thread(target=bridge.spin, name="rclpy-spin", daemon=True)
    spin_thread.start()

    try:
        uvicorn.run(
            app, host=config.http_host, port=config.http_port, log_level="info"
        )
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    main()
