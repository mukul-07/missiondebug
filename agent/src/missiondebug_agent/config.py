"""Pydantic config schema for the agent. Loaded from YAML at startup."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class TopicConfig(BaseModel):
    name: str
    type: str


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


class AnomalyConfig(BaseModel):
    stall: StallConfig = StallConfig()
    path_deviation: PathDeviationConfig | None = None  # opt-in for v1

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
