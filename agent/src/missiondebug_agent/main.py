"""Agent entry point. Wires config -> ring buffer -> ROS bridge ->
anomaly detectors -> HTTP API, runs everything until SIGINT.
"""

from __future__ import annotations

import argparse
import logging
import threading
from pathlib import Path

import uvicorn

from .config import AgentConfig
from .detectors.path_deviation import PathDeviationAnomaly, PathDeviationDetector
from .detectors.stall import StallAnomaly, StallDetector
from .http_api import build_app, save_now
from .ring_buffer import RingBuffer

log = logging.getLogger("missiondebug_agent")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _extract_pose_from_tf(msg, child_frame: str):
    """Find a transform with the matching child_frame_id and return (x, y).

    Returns None if not present in this TFMessage.
    """
    for tf in getattr(msg, "transforms", []):
        if tf.child_frame_id == child_frame:
            t = tf.transform.translation
            return (float(t.x), float(t.y))
    return None


def _path_to_waypoints(msg) -> list[tuple[float, float]]:
    """nav_msgs/msg/Path -> list of (x, y) in plan frame."""
    out = []
    for ps in getattr(msg, "poses", []):
        p = ps.pose.position
        out.append((float(p.x), float(p.y)))
    return out


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.example.yaml")
    args = parser.parse_args()

    config = AgentConfig.load(args.config)
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)

    ring = RingBuffer(window_seconds=config.buffer_seconds)
    app = build_app(config, ring)

    callbacks: dict = {}

    # ---------- Stall detector ----------
    stall_cfg = config.anomaly.resolved_stall()

    def on_stall(_a: StallAnomaly) -> None:
        log.info("Auto-saving session due to stall anomaly")
        try:
            r = save_now(config, ring, label="anomaly:stall")
            log.info("Auto-saved %s (%.2fs)", r.session_id, r.duration_s)
        except Exception:
            log.exception("Auto-save failed (stall)")

    stall_detector = StallDetector(
        velocity_threshold=stall_cfg.velocity_threshold,
        duration_seconds=stall_cfg.duration_seconds,
        cooldown_seconds=stall_cfg.cooldown_seconds,
        on_stall=on_stall,
    )

    def on_cmd_vel(msg, ts_ns: int) -> None:
        try:
            lin = float(msg.linear.x)
            ang = float(msg.angular.z)
        except AttributeError:
            log.exception("Unexpected /cmd_vel payload shape")
            raise
        stall_detector.update(lin, ang, ts_ns)

    callbacks["/cmd_vel"] = on_cmd_vel

    # ---------- Path-deviation detector (opt-in) ----------
    pd_cfg = config.anomaly.path_deviation
    if pd_cfg is not None:
        def on_path_deviation(a: PathDeviationAnomaly) -> None:
            log.info("Auto-saving session due to path-deviation anomaly")
            try:
                r = save_now(config, ring, label="anomaly:path-deviation")
                log.info("Auto-saved %s (%.2fs, drift %.2fm)",
                         r.session_id, r.duration_s, a.distance_m)
            except Exception:
                log.exception("Auto-save failed (path-deviation)")

        pd_detector = PathDeviationDetector(
            threshold_meters=pd_cfg.threshold_meters,
            duration_seconds=pd_cfg.duration_seconds,
            cooldown_seconds=pd_cfg.cooldown_seconds,
            on_anomaly=on_path_deviation,
        )

        def on_plan(msg, _ts_ns: int) -> None:
            try:
                pd_detector.update_plan(_path_to_waypoints(msg))
            except Exception:
                log.exception("Failed to parse /plan message")
                raise

        def on_pose(msg, ts_ns: int) -> None:
            try:
                pose = _extract_pose_from_tf(msg, pd_cfg.pose_child_frame)
            except Exception:
                log.exception("Failed to extract pose from /tf message")
                raise
            if pose is not None:
                pd_detector.update_pose(pose[0], pose[1], ts_ns)

        callbacks[pd_cfg.plan_topic] = on_plan
        callbacks[pd_cfg.pose_topic] = on_pose

    # ---------- ROS bridge ----------
    from .ros_bridge import RosBridge

    bridge = RosBridge(config, ring, message_callbacks=callbacks)

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
