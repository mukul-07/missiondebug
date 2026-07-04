"""GET /api/v1/agents/{robot_id}/topics — hub-as-proxy topic discovery.

Same test shape as test_files_proxy.py: a real local HTTP server plays the
agent (no httpx mocking), agents register through the public heartbeat
endpoint, sessions arrive through the public ingest endpoint.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from threading import Thread
from wsgiref.simple_server import make_server

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app

_TOPICS_PAYLOAD = {
    "settled": True,
    "topics": [
        {
            "name": "/cmd_vel",
            "type": "geometry_msgs/msg/Twist",
            "resolvable": True,
            "category": "control",
            "recommended": True,
            "reason": "control command (what the robot was told to do)",
            "large": False,
            "publishers": 1,
        },
        {
            "name": "/fmu/out/vehicle_odometry",
            "type": "px4_msgs/msg/VehicleOdometry",
            "resolvable": False,
            "category": "other",
            "recommended": False,
            "reason": None,
            "large": False,
            "publishers": 0,
        },
    ],
}


def _fake_agent_app(status: int = 200, body: bytes | None = None):
    """A WSGI app standing in for the agent's control API."""

    payload = body if body is not None else json.dumps(_TOPICS_PAYLOAD).encode()

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path != "/topics":
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"not found"]
        reason = {200: "OK", 404: "Not Found", 500: "Internal Server Error"}
        start_response(
            f"{status} {reason.get(status, 'OK')}",
            [("Content-Type", "application/json")],
        )
        return [payload]

    return app


@contextmanager
def _running_fake_agent(status: int = 200, body: bytes | None = None):
    server = make_server("127.0.0.1", 0, _fake_agent_app(status=status, body=body))
    port = server.server_address[1]
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _client(tmp_path) -> TestClient:
    return TestClient(
        build_app(sessions_dir=tmp_path / "sessions", db_path=tmp_path / "x.sqlite3")
    )


def _register(c: TestClient, robot_id: str, agent_url: str | None,
              agent_version: str | None = "0.7.4") -> None:
    payload: dict = {"robot_id": robot_id, "agent_version": agent_version}
    if agent_url is not None:
        payload["agent_url"] = agent_url
    r = c.post("/api/v1/agents/heartbeat", json=payload)
    assert r.status_code == 204


def test_topics_proxied_through_hub(tmp_path):
    with _running_fake_agent() as base:
        c = _client(tmp_path)
        _register(c, "r1", base)
        r = c.get("/api/v1/agents/r1/topics")
        assert r.status_code == 200
        j = r.json()
        assert j["robot_id"] == "r1"
        assert j["agent_version"] == "0.7.4"
        assert j["settled"] is True
        assert j["topics"] == _TOPICS_PAYLOAD["topics"]
        # No sessions for this robot yet -> no last-capture cross-reference.
        assert j["last_capture_topics"] is None
        assert j["last_capture_session_id"] is None


def test_topics_enriched_with_last_capture(tmp_path):
    with _running_fake_agent() as base:
        c = _client(tmp_path)
        _register(c, "r1", base)
        r = c.post(
            "/api/v1/sessions/ingest",
            json={
                "session_id": "SES-1",
                "robot_id": "r1",
                "started_at": 1_000,
                "ended_at": 61_000,
                "duration_ms": 60_000,
                "topics": ["/cmd_vel", "/tf"],
                "mcap_size_bytes": 123,
            },
        )
        assert r.status_code == 200
        j = c.get("/api/v1/agents/r1/topics").json()
        assert j["last_capture_topics"] == ["/cmd_vel", "/tf"]
        assert j["last_capture_session_id"] == "SES-1"


def test_topics_last_capture_is_most_recent_session(tmp_path):
    with _running_fake_agent() as base:
        c = _client(tmp_path)
        _register(c, "r1", base)
        for sid, started, topics in [
            ("SES-old", 1_000, ["/old_topic"]),
            ("SES-new", 999_000, ["/new_topic"]),
        ]:
            c.post(
                "/api/v1/sessions/ingest",
                json={
                    "session_id": sid,
                    "robot_id": "r1",
                    "started_at": started,
                    "ended_at": started + 60_000,
                    "duration_ms": 60_000,
                    "topics": topics,
                    "mcap_size_bytes": 1,
                },
            )
        j = c.get("/api/v1/agents/r1/topics").json()
        assert j["last_capture_topics"] == ["/new_topic"]


def test_topics_unknown_robot_404(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/v1/agents/nope/topics")
    assert r.status_code == 404


def test_topics_no_agent_url_409(tmp_path):
    c = _client(tmp_path)
    _register(c, "uds-robot", None)
    r = c.get("/api/v1/agents/uds-robot/topics")
    assert r.status_code == 409
    assert "agent_url" in r.json()["detail"]


def test_topics_agent_unreachable_502(tmp_path):
    c = _client(tmp_path)
    _register(c, "r1", "http://127.0.0.1:1")
    r = c.get("/api/v1/agents/r1/topics")
    assert r.status_code == 502
    assert "unreachable" in r.json()["detail"]


def test_topics_old_agent_426(tmp_path):
    # Pre-0.7.0 agents have no /topics route: the upstream 404 must surface
    # as an actionable upgrade message, not a generic proxy error.
    with _running_fake_agent(status=404) as base:
        c = _client(tmp_path)
        _register(c, "r1", base + "/definitely-not-topics-root",
                  agent_version="0.6.2")
        # Point agent_url at a path whose /topics suffix 404s on the fake.
        r = c.get("/api/v1/agents/r1/topics")
        assert r.status_code == 426
        assert "0.7.0" in r.json()["detail"]
        assert "0.6.2" in r.json()["detail"]


def test_topics_upstream_500_maps_to_502(tmp_path):
    with _running_fake_agent(status=500) as base:
        c = _client(tmp_path)
        _register(c, "r1", base)
        r = c.get("/api/v1/agents/r1/topics")
        assert r.status_code == 502


def test_topics_invalid_json_maps_to_502(tmp_path):
    with _running_fake_agent(body=b"this is not json") as base:
        c = _client(tmp_path)
        _register(c, "r1", base)
        r = c.get("/api/v1/agents/r1/topics")
        assert r.status_code == 502


def test_topics_unexpected_payload_maps_to_502(tmp_path):
    with _running_fake_agent(body=json.dumps({"topics": "nope"}).encode()) as base:
        c = _client(tmp_path)
        _register(c, "r1", base)
        r = c.get("/api/v1/agents/r1/topics")
        assert r.status_code == 502
