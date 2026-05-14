"""v2 P2.1 — fleet operational health endpoint.

GET /api/v1/agents/health returns aggregate counts + per-agent status.
Status is one of healthy / stale / silent based on heartbeat recency.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from missiondebug_backend.db import Db, now_ms
from missiondebug_backend.main import build_app


def _client(tmp_path) -> TestClient:
    return TestClient(
        build_app(
            sessions_dir=tmp_path / "sessions",
            db_path=tmp_path / "x.sqlite3",
        )
    )


def _hand_set_heartbeat(tmp_path, robot_id: str, last_heartbeat: int) -> None:
    """Drop straight into the DB to set an arbitrary heartbeat timestamp —
    needed for testing thresholds without sleeping for minutes."""
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_agent(robot_id=robot_id, agent_url=f"http://{robot_id}:7000")
    with db.connect() as conn:
        conn.execute(
            "UPDATE agents SET last_heartbeat = ? WHERE robot_id = ?",
            (last_heartbeat, robot_id),
        )
        conn.commit()


def test_health_empty_fleet(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/v1/agents/health")
    assert r.status_code == 200
    body = r.json()
    assert body["healthy"] == 0
    assert body["stale"] == 0
    assert body["silent"] == 0
    assert body["total"] == 0
    assert body["agents"] == []
    assert body["thresholds"]["healthy_seconds"] > 0
    assert body["thresholds"]["stale_seconds"] > body["thresholds"]["healthy_seconds"]


def test_health_fresh_heartbeat_is_healthy(tmp_path):
    c = _client(tmp_path)
    c.post("/api/v1/agents/heartbeat", json={"robot_id": "r1"})
    body = c.get("/api/v1/agents/health").json()
    assert body["healthy"] == 1
    assert body["stale"] == 0
    assert body["silent"] == 0
    assert body["total"] == 1
    assert len(body["agents"]) == 1
    a = body["agents"][0]
    assert a["robot_id"] == "r1"
    assert a["status"] == "healthy"
    # Heartbeat just posted; silence is near zero.
    assert a["silence_seconds"] < 5


def test_health_old_heartbeat_classifies_correctly(tmp_path):
    """Default thresholds: healthy <120s, stale 120-300s, silent ≥300s.
    Hand-set timestamps to validate each band."""
    now = now_ms()
    # Healthy: 30 s ago
    _hand_set_heartbeat(tmp_path, "fresh", now - 30 * 1000)
    # Stale: 180 s ago (between 120 and 300)
    _hand_set_heartbeat(tmp_path, "lagging", now - 180 * 1000)
    # Silent: 10 min ago
    _hand_set_heartbeat(tmp_path, "dead", now - 600 * 1000)

    c = _client(tmp_path)
    body = c.get("/api/v1/agents/health").json()
    assert body["healthy"] == 1
    assert body["stale"] == 1
    assert body["silent"] == 1
    assert body["total"] == 3

    by_id = {a["robot_id"]: a for a in body["agents"]}
    assert by_id["fresh"]["status"] == "healthy"
    assert by_id["lagging"]["status"] == "stale"
    assert by_id["dead"]["status"] == "silent"


def test_health_never_heartbeated_is_silent(tmp_path):
    """An agent in the table with last_heartbeat=NULL (e.g. registered
    via ingest but never pinged separately) is treated as silent with
    silence_seconds=-1 to signal 'never seen alive'."""
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_agent(robot_id="ghost", agent_url="http://ghost:7000")
    # No heartbeat set.

    c = _client(tmp_path)
    body = c.get("/api/v1/agents/health").json()
    assert body["silent"] == 1
    a = body["agents"][0]
    assert a["robot_id"] == "ghost"
    assert a["status"] == "silent"
    assert a["last_heartbeat"] is None
    assert a["silence_seconds"] == -1


def test_health_sort_order_silent_first(tmp_path):
    """Operator's eye should land on the worst case first.
    Order: silent → stale → healthy. Within each: most-silent first."""
    now = now_ms()
    _hand_set_heartbeat(tmp_path, "healthy-a", now - 10 * 1000)
    _hand_set_heartbeat(tmp_path, "healthy-b", now - 60 * 1000)
    _hand_set_heartbeat(tmp_path, "stale-a", now - 200 * 1000)
    _hand_set_heartbeat(tmp_path, "stale-b", now - 250 * 1000)
    _hand_set_heartbeat(tmp_path, "silent-a", now - 700 * 1000)
    _hand_set_heartbeat(tmp_path, "silent-b", now - 1000 * 1000)

    c = _client(tmp_path)
    rows = c.get("/api/v1/agents/health").json()["agents"]
    statuses = [r["status"] for r in rows]
    # All silent come before any stale; all stale before any healthy.
    silent_idx = [i for i, s in enumerate(statuses) if s == "silent"]
    stale_idx = [i for i, s in enumerate(statuses) if s == "stale"]
    healthy_idx = [i for i, s in enumerate(statuses) if s == "healthy"]
    assert max(silent_idx) < min(stale_idx)
    assert max(stale_idx) < min(healthy_idx)
    # Within silent, the one silent longest is first.
    silent_ids = [rows[i]["robot_id"] for i in silent_idx]
    assert silent_ids[0] == "silent-b"  # 1000s > 700s


def test_health_recovery_from_stale_to_healthy(tmp_path):
    """An agent that was stale becomes healthy after a fresh heartbeat."""
    now = now_ms()
    _hand_set_heartbeat(tmp_path, "r1", now - 200 * 1000)
    c = _client(tmp_path)
    body = c.get("/api/v1/agents/health").json()
    assert body["agents"][0]["status"] == "stale"

    # Post a fresh heartbeat — should flip to healthy.
    c.post("/api/v1/agents/heartbeat", json={"robot_id": "r1"})
    body = c.get("/api/v1/agents/health").json()
    assert body["healthy"] == 1
    assert body["stale"] == 0
    assert body["agents"][0]["status"] == "healthy"


def test_health_env_thresholds_respected(tmp_path, monkeypatch):
    """Custom thresholds via env vars apply at import time. This test
    re-imports the module with patched envs to verify the lookup."""
    monkeypatch.setenv("MD_HUB_HEALTHY_TIMEOUT_SEC", "10")
    monkeypatch.setenv("MD_HUB_STALE_TIMEOUT_SEC", "20")
    # The module-level constants were already read at import time, so
    # reimport to pick up the new env.
    import importlib

    import missiondebug_backend.routes.agents as agents_mod

    importlib.reload(agents_mod)
    assert agents_mod.HEALTHY_TIMEOUT_SEC == 10
    assert agents_mod.STALE_TIMEOUT_SEC == 20

    # Restore default by clearing env and reloading so other tests keep
    # the default thresholds.
    monkeypatch.delenv("MD_HUB_HEALTHY_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("MD_HUB_STALE_TIMEOUT_SEC", raising=False)
    importlib.reload(agents_mod)
    assert agents_mod.HEALTHY_TIMEOUT_SEC == 120
    assert agents_mod.STALE_TIMEOUT_SEC == 300


def test_health_carries_subsystem_and_metadata(tmp_path):
    """Each row mirrors the fields the AgentHealth UI page wants — robot_id,
    status, last_heartbeat, silence_seconds, subsystem, agent_version,
    agent_url."""
    c = _client(tmp_path)
    c.post("/api/v1/agents/heartbeat", json={
        "robot_id": "r1",
        "agent_version": "1.5.0",
        "agent_url": "http://r1:7000",
    })
    # Ingest a session to set the subsystem.
    c.post("/api/v1/sessions/ingest", json={
        "session_id": "x", "robot_id": "r1",
        "started_at": 0, "ended_at": 1, "duration_ms": 1,
        "topics": [], "mcap_size_bytes": 0,
        "mcap_url": "http://r1:7000/api/sessions/x/mcap",
        "subsystem": "navigation",
    })
    a = c.get("/api/v1/agents/health").json()["agents"][0]
    assert a["robot_id"] == "r1"
    assert a["status"] == "healthy"
    assert a["agent_version"] == "1.5.0"
    assert a["agent_url"] == "http://r1:7000"
    assert a["subsystem"] == "navigation"
    assert a["first_seen"] > 0
    assert a["last_heartbeat"] is not None


def test_list_agents_endpoint_unchanged(tmp_path):
    """The raw /api/v1/agents endpoint MUST stay simple — no health
    computation. /health is the surface that does that work."""
    c = _client(tmp_path)
    c.post("/api/v1/agents/heartbeat", json={"robot_id": "r1"})
    body = c.get("/api/v1/agents").json()
    assert "agents" in body
    a = body["agents"][0]
    assert "status" not in a
    assert "silence_seconds" not in a
