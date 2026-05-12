"""v2 P1.2: agent → hub ingest of session metadata, and heartbeat plumbing."""

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app


def _client(tmp_path) -> TestClient:
    app = build_app(
        sessions_dir=tmp_path / "sessions",
        db_path=tmp_path / "x.sqlite3",
    )
    return TestClient(app)


def _ingest_payload(**overrides) -> dict:
    base = {
        "session_id": "robot-001_20260512T100000Z",
        "robot_id": "robot-001",
        "started_at": 1_700_000_000_000,
        "ended_at": 1_700_000_060_000,
        "duration_ms": 60_000,
        "label": "anomaly:stall",
        "topics": ["/cmd_vel", "/odom"],
        "mcap_size_bytes": 12345,
        "mcap_url": "http://robot-001.local:7000/api/sessions/robot-001_20260512T100000Z/mcap",
        "subsystem": "navigation",
        "agent_url": "http://robot-001.local:7000",
        "agent_version": "1.5.0",
    }
    base.update(overrides)
    return base


def test_ingest_creates_session_and_agent(tmp_path):
    """Posting a session to /api/v1/sessions/ingest stores it + registers
    the agent. The session then appears in the regular /api/sessions list."""
    c = _client(tmp_path)

    r = c.post("/api/v1/sessions/ingest", json=_ingest_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"session_id": "robot-001_20260512T100000Z", "ingested": True}

    # Session shows up in the normal list with v2 fields surfaced (P1.6).
    sessions = c.get("/api/sessions").json()["sessions"]
    assert len(sessions) == 1
    s = sessions[0]
    assert s["robot_id"] == "robot-001"
    assert s["label"] == "anomaly:stall"
    assert s["subsystem"] == "navigation"
    assert s["source"] == "agent"  # hub-ingested via P1.2

    # Agent was registered.
    agents = c.get("/api/v1/agents").json()["agents"]
    assert len(agents) == 1
    a = agents[0]
    assert a["robot_id"] == "robot-001"
    assert a["agent_url"] == "http://robot-001.local:7000"
    assert a["agent_version"] == "1.5.0"
    assert a["subsystem"] == "navigation"
    assert a["last_heartbeat"] is None  # ingest doesn't tick the heartbeat


def test_ingest_idempotent(tmp_path):
    """Re-ingesting the same session_id replaces the row (no duplicate)."""
    c = _client(tmp_path)
    c.post("/api/v1/sessions/ingest", json=_ingest_payload())
    c.post("/api/v1/sessions/ingest", json=_ingest_payload(label="anomaly:updated"))
    sessions = c.get("/api/sessions").json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["label"] == "anomaly:updated"


def test_ingest_minimal_payload(tmp_path):
    """subsystem, agent_url, agent_version, label, topics are all optional."""
    c = _client(tmp_path)
    r = c.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": "x",
            "robot_id": "r",
            "started_at": 0,
            "ended_at": 1000,
            "duration_ms": 1000,
            "mcap_size_bytes": 0,
            "mcap_url": "http://r:7000/api/sessions/x/mcap",
        },
    )
    assert r.status_code == 200, r.text


def test_ingest_rejects_missing_required(tmp_path):
    """Pydantic enforces required fields."""
    c = _client(tmp_path)
    r = c.post("/api/v1/sessions/ingest", json={"session_id": "x"})
    assert r.status_code == 422


def test_heartbeat_creates_agent_on_first_ping(tmp_path):
    """A heartbeat from an unknown agent auto-creates the row."""
    c = _client(tmp_path)
    r = c.post(
        "/api/v1/agents/heartbeat",
        json={"robot_id": "r1", "agent_url": "http://r1:7000", "buffer_size": 42},
    )
    assert r.status_code == 204
    agents = c.get("/api/v1/agents").json()["agents"]
    assert len(agents) == 1
    assert agents[0]["robot_id"] == "r1"
    assert agents[0]["last_heartbeat"] is not None
    assert agents[0]["agent_url"] == "http://r1:7000"


def test_heartbeat_updates_existing_agent(tmp_path):
    """Subsequent heartbeats advance last_heartbeat without resetting first_seen."""
    c = _client(tmp_path)
    c.post("/api/v1/agents/heartbeat", json={"robot_id": "r1"})
    first = c.get("/api/v1/agents").json()["agents"][0]
    c.post("/api/v1/agents/heartbeat", json={"robot_id": "r1", "buffer_size": 99})
    second = c.get("/api/v1/agents").json()["agents"][0]
    assert second["first_seen"] == first["first_seen"]
    # last_heartbeat monotonically advances (or stays equal if within same ms).
    assert second["last_heartbeat"] >= first["last_heartbeat"]


def test_heartbeat_rejects_missing_robot_id(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/v1/agents/heartbeat", json={})
    assert r.status_code == 422


def test_list_agents_empty(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/v1/agents")
    assert r.status_code == 200
    assert r.json() == {"agents": []}
