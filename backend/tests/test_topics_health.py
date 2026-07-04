"""topics_health on the heartbeat: persisted on the agent row, surfaced on
the roster and fleet-health endpoints, cleared when an agent stops sending it."""

from __future__ import annotations

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app

HEALTH = {"ok": 2, "missing": [], "silent": ["/odom"], "unresolvable": ["/thing"]}


def _client(tmp_path) -> TestClient:
    return TestClient(
        build_app(sessions_dir=tmp_path / "sessions", db_path=tmp_path / "x.sqlite3")
    )


def _beat(c, robot_id="r1", **extra):
    r = c.post(
        "/api/v1/agents/heartbeat",
        json={"robot_id": robot_id, "agent_version": "0.8.0", **extra},
    )
    assert r.status_code == 204


def test_topics_health_persists_and_surfaces(tmp_path):
    c = _client(tmp_path)
    _beat(c, topics_health=HEALTH)

    roster = c.get("/api/v1/agents").json()["agents"]
    assert roster[0]["topics_health"] == HEALTH

    health = c.get("/api/v1/agents/health").json()["agents"]
    assert health[0]["topics_health"] == HEALTH


def test_topics_health_absent_is_null(tmp_path):
    c = _client(tmp_path)
    _beat(c)  # older agent: no field
    assert c.get("/api/v1/agents").json()["agents"][0]["topics_health"] is None


def test_topics_health_updates_and_clears(tmp_path):
    c = _client(tmp_path)
    _beat(c, topics_health=HEALTH)
    # Next heartbeat with everything healthy replaces the verdict…
    good = {"ok": 3, "missing": [], "silent": [], "unresolvable": []}
    _beat(c, topics_health=good)
    assert c.get("/api/v1/agents").json()["agents"][0]["topics_health"] == good
    # …and a heartbeat WITHOUT the field clears it (agent downgraded / ROS
    # gone) rather than pinning a stale verdict to the row.
    _beat(c)
    assert c.get("/api/v1/agents").json()["agents"][0]["topics_health"] is None
