"""Pydantic config schema for the agent. Loaded from YAML at startup."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class TopicConfig(BaseModel):
    name: str
    type: str


class AnomalyConfig(BaseModel):
    stall_velocity_threshold: float = 0.01
    stall_duration_seconds: float = 5.0
    cooldown_seconds: float = 30.0


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
