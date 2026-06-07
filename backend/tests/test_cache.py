"""TTL cache for the compute-heavy read endpoints + its write-invalidation.

Proves the two behaviours that matter: (1) the dashboard is served from cache
between writes (so concurrent reads don't all recompute), and (2) a write
clears the cache so the dashboard reflects the change immediately — no stale
KPIs after an edit.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from missiondebug_backend.cache import TTLCache
from missiondebug_backend.db import Db, SessionRow, now_ms
from missiondebug_backend.main import build_app


def test_ttl_cache_get_set_clear():
    c = TTLCache(ttl_seconds=100)
    assert c.get("k") is None
    c.set("k", 42)
    assert c.get("k") == 42
    c.clear()
    assert c.get("k") is None


def test_ttl_cache_expires():
    c = TTLCache(ttl_seconds=0.05)
    c.set("k", 1)
    assert c.get("k") == 1
    time.sleep(0.1)
    assert c.get("k") is None


def _mk(db: Db, sid: str, started: int) -> None:
    db.upsert_session(SessionRow(
        id=sid, robot_id="r", started_at=started, ended_at=started + 1,
        duration_ms=1, label="anomaly:x", mcap_path="", mcap_size_bytes=1,
        topics=[], created_at=started, summary="x",
    ))


def test_dashboard_cached_then_invalidated_on_write(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    app = build_app(tmp_path / "s", db_path)
    db = Db(db_path)
    now = now_ms()
    _mk(db, "S1", now - 1000)

    with TestClient(app) as c:
        first = c.get("/api/v2/fleet/incident-stats?window_days=30").json()
        assert first["captures"]["total"] == 1

        # Add a session WITHOUT an API write — the dashboard should still serve
        # the cached total (this is what makes concurrent reads cheap).
        _mk(db, "S2", now - 2000)
        cached = c.get("/api/v2/fleet/incident-stats?window_days=30").json()
        assert cached["captures"]["total"] == 1   # served from cache

        # An API write (a resolution edit) clears the cache → next read is fresh.
        assert c.put("/api/v2/sessions/S1/resolution",
                     json={"status": "investigating"}).status_code == 200
        fresh = c.get("/api/v2/fleet/incident-stats?window_days=30").json()
        assert fresh["captures"]["total"] == 2     # recomputed, sees S2
