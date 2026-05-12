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
    velocity_threshold: float = 0.01
    duration_seconds: float = 5.0
    cooldown_seconds: float = 30.0


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

    @classmethod
    def load(cls, path: str | Path) -> AgentConfig:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)
