"""Pydantic config schema for the agent. Loaded from YAML at startup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class TopicConfig(BaseModel):
    name: str
    type: str
    # v1.5: per-topic capture controls. Defaults preserve v1 behavior.
    rate_divisor: int = Field(default=1, ge=1)  # keep every Nth message; 1 = all
    ring_seconds: float | None = None             # None = use global buffer_seconds


class StallConfig(BaseModel):
    # A stall is "commanded to move but not actually moving". We compare the
    # commanded velocity (/cmd_vel) against the actual velocity from odometry
    # (motion_topic), so a robot that is legitimately parked (no command to
    # move) does NOT trigger. The motion_topic must be in `topics` (captured)
    # for the comparison to run; otherwise stall stays silent (no false fires).
    velocity_threshold: float = 0.01        # commanded: is the robot told to move?
    motion_threshold: float | None = None   # actual: is it moving? (defaults to velocity_threshold)
    motion_topic: str = "/odom"             # actual-velocity source (nav_msgs/Odometry)
    duration_seconds: float = 5.0
    cooldown_seconds: float = 30.0
    stale_seconds: float = 1.0              # ignore cmd/motion readings older than this


class PathDeviationConfig(BaseModel):
    threshold_meters: float = 0.5
    duration_seconds: float = 2.0
    cooldown_seconds: float = 30.0
    plan_topic: str = "/plan"
    pose_topic: str = "/tf"
    pose_child_frame: str = "base_link"


class RuleConfig(BaseModel):
    """A single config-driven anomaly detector rule (v1.5).

    Fires when `field` on `topic`'s incoming message matches the configured
    condition continuously for `duration_seconds`. Cooldown prevents
    re-firing on the same sustained condition.
    """

    name: str
    topic: str
    field: str  # dot-path, e.g. "data" or "status.status" or "linear.x"
    duration_seconds: float = Field(default=0.0, ge=0.0)
    cooldown_seconds: float = Field(default=30.0, ge=0.0)

    # Exactly one condition must be set.
    equals: Any | None = None
    not_equals: Any | None = None
    lt: float | None = None
    gt: float | None = None
    lte: float | None = None
    gte: float | None = None

    @model_validator(mode="after")
    def _exactly_one_condition(self) -> RuleConfig:
        conditions = [
            self.equals, self.not_equals,
            self.lt, self.gt, self.lte, self.gte,
        ]
        n_set = sum(1 for c in conditions if c is not None)
        if n_set != 1:
            raise ValueError(
                f"rule {self.name!r}: exactly one of "
                "equals/not_equals/lt/gt/lte/gte must be set"
            )
        return self


class TopicDropoutConfig(BaseModel):
    """Watch a topic and fire when no message arrives for `silence_seconds`."""
    topic: str
    silence_seconds: float = Field(gt=0)
    cooldown_seconds: float = Field(default=60.0, ge=0)
    name: str | None = None  # defaults to "dropout:<topic>" if omitted


class BatteryLowConfig(BaseModel):
    """Convenience wrapper that desugars to a numeric_threshold rule on
    a BatteryState topic. Just nicer config syntax than writing a raw rule."""
    topic: str
    threshold: float = 0.20  # 20% by default
    field: str = "percentage"  # BatteryState.percentage; override for custom msgs
    duration_seconds: float = Field(default=5.0, ge=0)
    cooldown_seconds: float = Field(default=600.0, ge=0)  # 10 min — not urgent
    name: str = "battery_low"


class AnomalyConfig(BaseModel):
    stall: StallConfig = StallConfig()
    path_deviation: PathDeviationConfig | None = None  # opt-in for v1
    rules: list[RuleConfig] = Field(default_factory=list)  # v1.5
    topic_dropout: list[TopicDropoutConfig] = Field(default_factory=list)  # v1.5
    battery_low: BatteryLowConfig | None = None  # v1.5

    def all_rules(self) -> list[RuleConfig]:
        """rules + auto-generated rules from convenience configs (battery_low)."""
        out = list(self.rules)
        if self.battery_low is not None:
            from .detectors.battery_low import to_rule
            out.append(to_rule(self.battery_low))
        return out

    # Backward-compat for v0 flat schema, in case anyone is still on it.
    stall_velocity_threshold: float | None = None
    stall_duration_seconds: float | None = None
    cooldown_seconds: float | None = None

    def resolved_stall(self) -> StallConfig:
        if self.stall_velocity_threshold is not None or self.stall_duration_seconds is not None:
            return StallConfig(
                velocity_threshold=self.stall_velocity_threshold or self.stall.velocity_threshold,
                duration_seconds=self.stall_duration_seconds or self.stall.duration_seconds,
                cooldown_seconds=self.cooldown_seconds or self.stall.cooldown_seconds,
            )
        return self.stall


class HubConfig(BaseModel):
    """v2 (fleet): optional config to push session metadata + heartbeats
    to a central hub. When unset, the agent runs in v1.5 standalone mode
    (Hard Rule 18: single-robot path must keep working unchanged)."""

    url: str | None = None
    auth_token: str | None = None
    heartbeat_interval_seconds: float = Field(default=60.0, gt=0)
    # The URL the hub uses to reach this agent back. Defaults to
    # http://<http_host>:<http_port> if unset, but operators can override
    # when the agent sits behind a NAT or reverse proxy.
    agent_url: str | None = None
    # Optional free-form subsystem tag attached to every session this
    # agent reports. Hard Rule 23: no enforced hierarchy.
    subsystem: str | None = None


class S3Config(BaseModel):
    """v2 P5a — optional MCAP upload to an S3-compatible object store.

    When set, the agent uploads each MCAP file to the bucket after the
    local write succeeds, then reports the public HTTPS URL as the
    session's mcap_url. This decouples MCAP durability from robot disk
    health — if the robot dies, sessions still scrub from the hub.

    Hard Rule 19 (MCAPs do not auto-upload by default) holds: this is
    opt-in. v1.5 single-robot deployments don't set s3.bucket and never
    touch S3.

    Compatible with AWS S3, MinIO, Cloudflare R2, GCS via S3-interop,
    and any other S3-compatible service. `endpoint_url` is unset for
    AWS, set to the MinIO/R2 endpoint otherwise.

    public_base_url: the prefix the hub uses to fetch objects. For AWS
    this is typically https://<bucket>.s3.<region>.amazonaws.com.
    Customers fronting S3 with CloudFront or a CDN set this to the CDN
    domain. v2.1+ work moves to hub-side presigned URLs for proper auth;
    v2 P5a expects a publicly-readable bucket or a CDN with edge auth.
    """

    bucket: str | None = None
    region: str = "us-east-1"
    endpoint_url: str | None = None
    public_base_url: str | None = None
    # Credentials. Boto3 also reads from env / ~/.aws/credentials when
    # these are unset, which is the recommended production path.
    access_key_id: str | None = None
    secret_access_key: str | None = None
    # Key prefix inside the bucket. Resolves to:
    #   <prefix>/<robot_id>/<session_id>.mcap
    key_prefix: str = "missiondebug/sessions"


class AgentConfig(BaseModel):
    robot_id: str = "robot-001"
    buffer_seconds: float = Field(default=60.0, gt=0)
    # v1.5: optional global RAM cap across all topic buffers (bytes).
    # When exceeded, oldest entries across all topics drop until under cap.
    # None = no global cap (rely on per-topic windows alone).
    max_total_bytes: int | None = Field(default=None, ge=0)
    topics: list[TopicConfig]
    output_dir: str = "./sessions"
    http_host: str = "127.0.0.1"
    http_port: int = 7000
    anomaly: AnomalyConfig = AnomalyConfig()
    # v2 (fleet): optional hub registration. Empty = standalone v1.5 mode.
    hub: HubConfig = HubConfig()
    # v2 P5a: optional MCAP upload to S3-compatible storage. Empty =
    # no upload (Hard Rule 19). When bucket is set, every saved session
    # also gets uploaded.
    s3: S3Config = S3Config()

    @classmethod
    def load(cls, path: str | Path) -> AgentConfig:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)
