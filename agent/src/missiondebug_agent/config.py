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


class AnomalyConfig(BaseModel):
    stall: StallConfig = StallConfig()
    path_deviation: PathDeviationConfig | None = None  # opt-in for v1
    rules: list[RuleConfig] = Field(default_factory=list)  # v1.5

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

    @classmethod
    def load(cls, path: str | Path) -> AgentConfig:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)
