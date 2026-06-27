"""Item 1a: agent caches its most recent capture (any trigger) and serves
it at GET /sessions/last, so the Transitive shim can surface anomaly
captures in the portal.

Additive + backward compatible: a LastSessionCache is optional on
save_now/build_app, and /sessions/last 404s until the first capture.

Tests call the route handler closure directly (like the rest of the
suite) instead of FastAPI's TestClient, which would pull in httpx.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from missiondebug_agent.config import AgentConfig, TopicConfig
from missiondebug_agent.http_api import (
    LastSessionCache,
    _trigger_from_label,
    build_app,
    save_now,
)
from missiondebug_agent.ring_buffer import BufferedMessage, RingBuffer


def _loader(_t: str) -> str:
    return "string data\n"


def _config(tmp_path: Path, robot_id: str = "robot-001") -> AgentConfig:
    return AgentConfig(
        robot_id=robot_id,
        buffer_seconds=60.0,
        topics=[TopicConfig(name="/cmd_vel", type="geometry_msgs/msg/Twist")],
        output_dir=str(tmp_path),
    )


def _ring(n: int = 5) -> RingBuffer:
    r = RingBuffer(window_seconds=60.0)
    for i in range(n):
        r.append(BufferedMessage(
            timestamp_ns=i * 100_000_000,
            wall_ns=1_700_000_000_000_000_000 + i * 100_000_000,
            topic="/cmd_vel",
            payload=b"\x00" * 4,
        ))
    return r


def _last_handler(app):
    route = next(r for r in app.routes if getattr(r, "path", None) == "/sessions/last")
    return route.endpoint


def test_trigger_from_label():
    assert _trigger_from_label("anomaly:stall") == "anomaly:stall"
    assert _trigger_from_label("anomaly:dropout:/scan") == "anomaly:dropout:/scan"
    assert _trigger_from_label(None) == "manual"
    assert _trigger_from_label("transitive:web-123") == "manual"
    assert _trigger_from_label("operator note") == "manual"


def test_last_session_404_before_any_capture(tmp_path):
    app = build_app(_config(tmp_path), _ring(), last_cache=LastSessionCache())
    with pytest.raises(HTTPException) as exc:
        _last_handler(app)()
    assert exc.value.status_code == 404


def test_empty_buffer_save_raises_diagnostic_409(tmp_path):
    """An empty-buffer capture explains WHY instead of a bare error.

    Reproduces Shane's case: a configured topic (px4_msgs) whose package is
    not built, so nothing is captured. The 409 detail should name the topics
    and point at the message-package cause, not just say 'ring buffer is empty'.
    """
    config = AgentConfig(
        robot_id="robot-001",
        buffer_seconds=60.0,
        topics=[TopicConfig(
            name="/fmu/out/vehicle_odometry",
            type="px4_msgs/msg/VehicleOdometry",
        )],
        output_dir=str(tmp_path),
    )
    empty_ring = RingBuffer(window_seconds=60.0)
    with pytest.raises(HTTPException) as exc:
        save_now(config, empty_ring, label=None, schema_loader=_loader)
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert "/fmu/out/vehicle_odometry" in detail
    assert "px4_msgs" in detail
    assert "message package" in detail


def test_empty_buffer_with_no_topics_configured(tmp_path):
    config = AgentConfig(
        robot_id="robot-001", buffer_seconds=60.0, topics=[],
        output_dir=str(tmp_path),
    )
    with pytest.raises(HTTPException) as exc:
        save_now(config, RingBuffer(window_seconds=60.0), schema_loader=_loader)
    assert exc.value.status_code == 409
    assert "no topics are configured" in exc.value.detail


def test_save_now_populates_cache_and_endpoint_reflects_it(tmp_path):
    """A manual save updates the cache; /sessions/last returns its shape."""
    cache = LastSessionCache()
    app = build_app(_config(tmp_path), _ring(), last_cache=cache)

    resp = save_now(
        _config(tmp_path), _ring(),
        label=None, schema_loader=_loader, last_cache=cache,
    )

    body = _last_handler(app)()
    assert body["session_id"] == resp.session_id
    assert body["trigger"] == "manual"
    assert body["topic_count"] == len(resp.topics)
    assert body["duration_s"] == resp.duration_s
    assert isinstance(body["saved_at_ms"], int)


def test_detector_capture_is_reflected_with_anomaly_trigger(tmp_path):
    """A detector-path save (anomaly label) shows the specific trigger."""
    cache = LastSessionCache()
    app = build_app(_config(tmp_path), _ring(), last_cache=cache)

    save_now(
        _config(tmp_path), _ring(),
        label="anomaly:dropout:/scan", schema_loader=_loader, last_cache=cache,
    )

    body = _last_handler(app)()
    assert body["trigger"] == "anomaly:dropout:/scan"
    assert body["label"] == "anomaly:dropout:/scan"


def test_save_now_without_cache_is_unchanged(tmp_path):
    """Backward compatible: no cache passed -> save still works, no error."""
    resp = save_now(_config(tmp_path), _ring(), label="test", schema_loader=_loader)
    assert resp.session_id.startswith("robot-001_")
    assert Path(resp.path).exists()
