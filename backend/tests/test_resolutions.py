"""v2 P3.5.6a — per-session resolution data layer + endpoints.

Covers the invariants the fleet incident dashboard depends on:

- Implicit-open shape for untriaged sessions (no row in DB)
- resolved_at is auto-managed on transitions (first-terminal stamp;
  preserved across terminal→terminal; cleared on terminal→open)
- duplicate_of validation (required + same-session + dangling)
- Status enum enforced via Pydantic pattern + db-layer ValueError
- DELETE reverts to implicit open

The MTTR + resolution-rate KPI computation lives in P3.5.6b — these
tests guarantee the raw fields feeding that math are correct.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from missiondebug_backend.db import (
    RESOLUTION_STATUSES,
    TERMINAL_STATUSES,
    Db,
    ResolutionRow,
)
from missiondebug_backend.main import build_app


# ============================================================
# Db layer — direct unit tests on the methods feeding the route
# ============================================================


def _db(tmp_path) -> Db:
    return Db(tmp_path / "x.sqlite3")


def _seed_session(tmp_path, sid: str = "sesn_001") -> Db:
    """Create a sessions row so resolutions can FK to it (the
    `session_resolutions.session_id → sessions.id` FK is enforced when
    PRAGMA foreign_keys=ON, which our connect() sets)."""
    from missiondebug_backend.db import SessionRow

    db = _db(tmp_path)
    db.upsert_session(SessionRow(
        id=sid,
        robot_id="robot-001",
        started_at=1_000,
        ended_at=1_060_000,
        duration_ms=60_000,
        label="anomaly:stall",
        mcap_path="",
        mcap_size_bytes=1024,
        topics=["/cmd_vel"],
        created_at=1_000,
    ))
    return db


def test_get_resolution_returns_none_when_no_row(tmp_path):
    """The route handler is what synthesises the implicit-open shape;
    the db layer below it just returns None so callers can distinguish
    "never triaged" from "explicitly set back to open"."""
    db = _seed_session(tmp_path)
    assert db.get_resolution("sesn_001") is None


def test_implicit_open_dataclass():
    """ResolutionRow.implicit_open is the shape the route hands the UI
    for untriaged sessions, so it should reflect the open default."""
    r = ResolutionRow.implicit_open("sesn_001")
    assert r.status == "open"
    assert r.resolved_at is None
    assert r.edited_at == 0  # sentinel for "this isn't a real row"


def test_upsert_resolution_stamps_resolved_at_on_first_terminal(tmp_path):
    """First time a session transitions to a terminal status, resolved_at
    is now_ms() — this is the timestamp MTTR aggregation reads."""
    db = _seed_session(tmp_path)
    r = db.upsert_resolution(
        session_id="sesn_001",
        status="resolved",
        root_cause="encoder drift",
    )
    assert r.status == "resolved"
    assert r.resolved_at is not None
    assert r.resolved_at > 0
    assert r.root_cause == "encoder drift"


def test_upsert_preserves_resolved_at_across_terminal_transitions(tmp_path):
    """MTTR measures time-to-FIRST-resolution. Reclassifying a resolved
    incident as a duplicate (or wont_fix) must not reset the clock."""
    db = _seed_session(tmp_path)
    first = db.upsert_resolution(session_id="sesn_001", status="resolved")
    original_ts = first.resolved_at
    assert original_ts is not None

    # Move to wont_fix — resolved_at must survive.
    second = db.upsert_resolution(session_id="sesn_001", status="wont_fix")
    assert second.resolved_at == original_ts


def test_upsert_clears_resolved_at_on_terminal_to_open(tmp_path):
    """If an operator un-resolves a session (re-opens it), the timestamp
    should go away — otherwise it'd still appear in MTTR rollups for the
    window it was briefly resolved in, which would be misleading."""
    db = _seed_session(tmp_path)
    db.upsert_resolution(session_id="sesn_001", status="resolved")
    r = db.upsert_resolution(session_id="sesn_001", status="investigating")
    assert r.status == "investigating"
    assert r.resolved_at is None


def test_upsert_rejects_unknown_status(tmp_path):
    db = _seed_session(tmp_path)
    with pytest.raises(ValueError):
        db.upsert_resolution(session_id="sesn_001", status="bogus")


def test_delete_reverts_to_implicit_open(tmp_path):
    db = _seed_session(tmp_path)
    db.upsert_resolution(session_id="sesn_001", status="resolved")
    assert db.delete_resolution("sesn_001") is True
    assert db.get_resolution("sesn_001") is None
    # Idempotent — deleting an already-absent row returns False, no error.
    assert db.delete_resolution("sesn_001") is False


def test_terminal_statuses_set_membership():
    """Sanity-check the constant the fleet stats endpoint will read."""
    assert TERMINAL_STATUSES == {"resolved", "duplicate", "wont_fix"}
    # And every terminal status is in the canonical list.
    assert TERMINAL_STATUSES <= set(RESOLUTION_STATUSES)


# ============================================================
# HTTP route — end-to-end through TestClient + real SQLite
# ============================================================


def _client(tmp_path) -> TestClient:
    app = build_app(
        sessions_dir=tmp_path / "sessions",
        db_path=tmp_path / "x.sqlite3",
    )
    return TestClient(app)


def _ingest_session(c: TestClient, sid: str = "sesn_001") -> None:
    c.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": sid,
            "robot_id": "robot-001",
            "started_at": 1_000,
            "ended_at": 61_000,
            "duration_ms": 60_000,
            "label": "anomaly:stall",
            "topics": ["/cmd_vel"],
            "mcap_size_bytes": 1024,
            "mcap_url": f"http://x/{sid}",
            "summary": "rule 'stall' on robot-001 /cmd_vel",
        },
    )


def test_get_returns_implicit_open_for_untriaged_session(tmp_path):
    """The dashboard expects every session to have a resolution shape,
    even untriaged ones. The route synthesises the open row."""
    c = _client(tmp_path)
    _ingest_session(c)
    r = c.get("/api/v2/sessions/sesn_001/resolution")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "open"
    assert body["resolved_at"] is None
    assert body["edited_at"] == 0  # sentinel


def test_get_returns_404_for_unknown_session(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/v2/sessions/no-such-session/resolution")
    assert r.status_code == 404


def test_put_resolved_with_root_cause(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c)
    r = c.put(
        "/api/v2/sessions/sesn_001/resolution",
        json={
            "status": "resolved",
            "root_cause": "encoder drift after firmware 2.3.1; downgraded to 2.3.0",
            "linked_ticket": "ENG-1247",
            "edited_by": "alice@example.com",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["root_cause"].startswith("encoder drift")
    assert body["linked_ticket"] == "ENG-1247"
    assert body["edited_by"] == "alice@example.com"
    assert body["resolved_at"] is not None


def test_put_invalid_status_rejected(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c)
    r = c.put(
        "/api/v2/sessions/sesn_001/resolution",
        json={"status": "in_progress"},  # close to but not 'investigating'
    )
    assert r.status_code == 422


def test_put_duplicate_requires_duplicate_of(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c)
    r = c.put(
        "/api/v2/sessions/sesn_001/resolution",
        json={"status": "duplicate"},
    )
    assert r.status_code == 422
    assert "duplicate_of" in r.text


def test_put_duplicate_of_self_rejected(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c)
    r = c.put(
        "/api/v2/sessions/sesn_001/resolution",
        json={"status": "duplicate", "duplicate_of": "sesn_001"},
    )
    assert r.status_code == 422


def test_put_duplicate_of_unknown_session_rejected(tmp_path):
    """The dashboard's duplicate-cluster rollup would accumulate dangling
    pointers if we let this through."""
    c = _client(tmp_path)
    _ingest_session(c)
    r = c.put(
        "/api/v2/sessions/sesn_001/resolution",
        json={"status": "duplicate", "duplicate_of": "does-not-exist"},
    )
    assert r.status_code == 422


def test_put_duplicate_valid(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c, "sesn_canonical")
    _ingest_session(c, "sesn_dup")
    r = c.put(
        "/api/v2/sessions/sesn_dup/resolution",
        json={"status": "duplicate", "duplicate_of": "sesn_canonical"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "duplicate"
    assert body["duplicate_of"] == "sesn_canonical"
    assert body["resolved_at"] is not None  # duplicate is a terminal state


def test_put_duplicate_of_cleared_for_non_duplicate_status(tmp_path):
    """duplicate_of only makes sense when status='duplicate'. Sending it
    with another status (e.g. resolved) silently clears it rather than
    erroring — the field is meaningless in that context."""
    c = _client(tmp_path)
    _ingest_session(c, "sesn_other")
    _ingest_session(c, "sesn_001")
    r = c.put(
        "/api/v2/sessions/sesn_001/resolution",
        json={
            "status": "resolved",
            "duplicate_of": "sesn_other",
        },
    )
    assert r.status_code == 200
    assert r.json()["duplicate_of"] is None


def test_put_then_get_round_trip(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c)
    c.put(
        "/api/v2/sessions/sesn_001/resolution",
        json={"status": "investigating", "root_cause": "looking into it"},
    )
    r = c.get("/api/v2/sessions/sesn_001/resolution")
    body = r.json()
    assert body["status"] == "investigating"
    assert body["root_cause"] == "looking into it"
    assert body["resolved_at"] is None


def test_delete_reverts_to_implicit_open(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c)
    c.put("/api/v2/sessions/sesn_001/resolution", json={"status": "resolved"})
    r = c.delete("/api/v2/sessions/sesn_001/resolution")
    assert r.status_code == 204
    # GET now returns the implicit-open shape again.
    g = c.get("/api/v2/sessions/sesn_001/resolution").json()
    assert g["status"] == "open"
    assert g["edited_at"] == 0


def test_delete_idempotent_on_untriaged_session(tmp_path):
    c = _client(tmp_path)
    _ingest_session(c)
    r = c.delete("/api/v2/sessions/sesn_001/resolution")
    assert r.status_code == 204  # not 404 — operation is idempotent


def test_put_404_for_unknown_session(tmp_path):
    c = _client(tmp_path)
    r = c.put(
        "/api/v2/sessions/no-such-session/resolution",
        json={"status": "resolved"},
    )
    assert r.status_code == 404
