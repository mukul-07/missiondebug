"""MD_CORS_ORIGINS on the agent API: opt-in only. Default is NO cross-origin
browser access (this API is loopback-first and unauthenticated); setting the
env allows a browser origin, e.g. the MissionDebug Foxglove panel calling
POST /sessions/save. Non-browser clients are unaffected either way.

Asserted at the wiring level (middleware present + origins parsed): the
agent's test env deliberately has no HTTP client dependency, and the
middleware's HTTP behavior is Starlette's, covered end-to-end by the
backend's test_cors_config.py against the same class.
"""

from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware

from missiondebug_agent.config import AgentConfig, TopicConfig
from missiondebug_agent.http_api import build_app
from missiondebug_agent.ring_buffer import RingBuffer

FOXGLOVE = "https://app.foxglove.dev"


def _config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        robot_id="robot-001",
        topics=[TopicConfig(name="/cmd_vel", type="geometry_msgs/msg/Twist")],
        output_dir=str(tmp_path / "sessions"),
    )


def _cors_middleware(app):
    return [m for m in app.user_middleware if m.cls is CORSMiddleware]


def test_default_no_cors_middleware(tmp_path, monkeypatch):
    monkeypatch.delenv("MD_CORS_ORIGINS", raising=False)
    app = build_app(_config(tmp_path), RingBuffer(window_seconds=60.0))
    assert _cors_middleware(app) == []


def test_empty_env_means_no_cors_middleware(tmp_path, monkeypatch):
    # compose passes "${VAR:-}" as an empty string — must behave like unset
    monkeypatch.setenv("MD_CORS_ORIGINS", "")
    app = build_app(_config(tmp_path), RingBuffer(window_seconds=60.0))
    assert _cors_middleware(app) == []


def test_env_adds_middleware_with_parsed_origins(tmp_path, monkeypatch):
    monkeypatch.setenv("MD_CORS_ORIGINS", f"{FOXGLOVE}, http://hub.internal:8000,")
    app = build_app(_config(tmp_path), RingBuffer(window_seconds=60.0))
    mws = _cors_middleware(app)
    assert len(mws) == 1
    assert mws[0].kwargs["allow_origins"] == [FOXGLOVE, "http://hub.internal:8000"]
    assert mws[0].kwargs["allow_credentials"] is False
