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
from .detectors.from_config import RuleAnomaly, RuleEngine
from .detectors.path_deviation import PathDeviationAnomaly, PathDeviationDetector
from .detectors.stall import StallAnomaly, StallDetector
from .detectors.topic_dropout import DropoutAnomaly, TopicDropoutDetector
from .http_api import LastSessionCache, build_app, save_now
from .hub_client import HubClient, HubClientConfig
from .ring_buffer import RingBuffer
# S3Uploader imported lazily inside main() so the boto3 dependency only
# gets pulled in when config.s3.bucket is actually set. v1.5 standalone
# deployments without the [s3] extra still load cleanly.

# Bumped per release; reported to the hub in every heartbeat so fleet
# operators can spot agents lagging behind on rollouts.
AGENT_VERSION = "0.2.0"

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

    ring = RingBuffer(
        window_seconds=config.buffer_seconds,
        max_total_bytes=config.max_total_bytes,
    )

    # v2 (fleet): optional hub registration. Started here so it's running by
    # the time the first save fires; stopped in the finally block below.
    hub_client: HubClient | None = None
    if config.hub.url:
        agent_url = config.hub.agent_url or f"http://{config.http_host}:{config.http_port}"
        hub_client = HubClient(HubClientConfig(
            hub_url=config.hub.url,
            robot_id=config.robot_id,
            agent_url=agent_url,
            auth_token=config.hub.auth_token,
            agent_version=AGENT_VERSION,
            subsystem=config.hub.subsystem,
            heartbeat_interval_seconds=config.hub.heartbeat_interval_seconds,
        ))
        hub_client.start()
        log.info("Hub sync enabled (url=%s, robot=%s)", config.hub.url, config.robot_id)

    # v2 P5a: optional S3 upload after each save. Lazy import so boto3
    # is only required when the customer actually opts in.
    s3_uploader = None
    if config.s3.bucket:
        try:
            from .s3_uploader import S3Uploader
            s3_uploader = S3Uploader(config.s3)
            log.info(
                "S3 upload enabled (bucket=%s, prefix=%s)",
                config.s3.bucket, config.s3.key_prefix,
            )
        except Exception:
            log.exception(
                "Failed to initialise S3 uploader — continuing without it. "
                "Sessions will only live on local disk."
            )

    # Cache of the most recent capture (any trigger), served by
    # GET /sessions/last so the Transitive shim can surface anomaly
    # captures in the portal. Shared by build_app and every save_now call.
    last_cache = LastSessionCache()

    app = build_app(
        config, ring,
        hub_client=hub_client, s3_uploader=s3_uploader, last_cache=last_cache,
    )

    # Multiple detectors may want callbacks on the same topic (e.g. /tf used
    # by path-deviation AND a config rule). Stack them per topic and present
    # a single function to the bridge.
    callbacks_by_topic: dict[str, list] = {}

    def add_cb(topic_name: str, cb) -> None:
        callbacks_by_topic.setdefault(topic_name, []).append(cb)

    # ---------- Stall detector ----------
    stall_cfg = config.anomaly.resolved_stall()

    def on_stall(_a: StallAnomaly) -> None:
        log.info("Auto-saving session due to stall anomaly")
        try:
            r = save_now(config, ring, label="anomaly:stall", hub_client=hub_client, s3_uploader=s3_uploader, last_cache=last_cache)
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

    add_cb("/cmd_vel", on_cmd_vel)

    # ---------- Path-deviation detector (opt-in) ----------
    pd_cfg = config.anomaly.path_deviation
    if pd_cfg is not None:
        def on_path_deviation(a: PathDeviationAnomaly) -> None:
            log.info("Auto-saving session due to path-deviation anomaly")
            try:
                r = save_now(config, ring, label="anomaly:path-deviation", hub_client=hub_client, s3_uploader=s3_uploader, last_cache=last_cache)
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

        add_cb(pd_cfg.plan_topic, on_plan)
        add_cb(pd_cfg.pose_topic, on_pose)

    # ---------- Config-driven rule engine (v1.5) ----------
    # all_rules() folds in convenience-detector configs (battery_low) by
    # converting them to plain RuleConfigs.
    rule_cfgs = config.anomaly.all_rules()
    if rule_cfgs:
        def on_rule_anomaly(a: RuleAnomaly) -> None:
            label = f"anomaly:{a.name}"
            log.info("Auto-saving session: rule %s", a.name)
            try:
                r = save_now(config, ring, label=label, hub_client=hub_client, s3_uploader=s3_uploader, last_cache=last_cache)
                log.info("Auto-saved %s (%.2fs, matched=%r)",
                         r.session_id, r.duration_s, a.matched_value)
            except Exception:
                log.exception("Auto-save failed (rule %s)", a.name)

        rule_engine = RuleEngine(rule_cfgs, on_anomaly=on_rule_anomaly)

        for topic_name in rule_engine.topics:
            # Bind topic_name into the closure to avoid late-binding bug.
            def make_cb(t):
                def cb(msg, ts_ns):
                    rule_engine.update(t, msg, ts_ns)
                return cb
            add_cb(topic_name, make_cb(topic_name))

        log.info("Loaded %d config-driven rule(s)", len(rule_cfgs))

    # ---------- Topic-dropout detector (v1.5) ----------
    dropout_cfgs = config.anomaly.topic_dropout
    dropout_detector: TopicDropoutDetector | None = None
    if dropout_cfgs:
        def on_dropout(a: DropoutAnomaly) -> None:
            log.info("Auto-saving session: dropout on %s", a.topic)
            try:
                r = save_now(config, ring, label=f"anomaly:{a.name}", hub_client=hub_client, s3_uploader=s3_uploader, last_cache=last_cache)
                log.info("Auto-saved %s (%.2fs, silence %.2fs)",
                         r.session_id, r.duration_s, a.silence_ns / 1e9)
            except Exception:
                log.exception("Auto-save failed (dropout %s)", a.topic)

        dropout_detector = TopicDropoutDetector(dropout_cfgs, on_anomaly=on_dropout)

        def make_dropout_cb(detector: TopicDropoutDetector, t: str):
            def cb(_msg, ts_ns: int) -> None:
                detector.saw(t, ts_ns)
            return cb

        for topic_name in dropout_detector.topics:
            add_cb(topic_name, make_dropout_cb(dropout_detector, topic_name))

        dropout_detector.start()
        log.info("Watching %d topic(s) for dropout", len(dropout_cfgs))

    # ---------- ROS bridge ----------
    from .ros_bridge import RosBridge

    # Compose multi-callbacks-per-topic into the single-callback shape the
    # bridge expects. Order is registration order.
    composed: dict = {}
    for topic_name, cb_list in callbacks_by_topic.items():
        def fan_out(msg, ts_ns, _cbs=cb_list):
            for c in _cbs:
                c(msg, ts_ns)
        composed[topic_name] = fan_out
    bridge = RosBridge(config, ring, message_callbacks=composed)

    spin_thread = threading.Thread(target=bridge.spin, name="rclpy-spin", daemon=True)
    spin_thread.start()

    try:
        uvicorn.run(
            app, host=config.http_host, port=config.http_port, log_level="info"
        )
    finally:
        if dropout_detector is not None:
            dropout_detector.stop()
        if hub_client is not None:
            hub_client.stop()
        bridge.shutdown()


if __name__ == "__main__":
    main()
