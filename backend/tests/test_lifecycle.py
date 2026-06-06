"""v2 P5b — age-based lifecycle policies (cold-tier + delete)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from missiondebug_backend.db import Db, SessionRow, now_ms
from missiondebug_backend.lifecycle import sweep_lifecycle_once
from missiondebug_backend.main import _env_int, build_app

_DAY_MS = 86_400_000


def _mk_session(
    db: Db,
    sessions_dir: Path,
    sid: str,
    *,
    started_at: int,
    size_bytes: int = 100,
    summary: str | None = "battery_low on bot across /battery_state",
    mcap_url: str | None = None,
    write_file: bool = True,
) -> Path | None:
    p: Path | None = None
    mcap_path = ""
    if write_file:
        p = sessions_dir / f"{sid}.mcap"
        p.write_bytes(b"\x00" * size_bytes)
        mcap_path = str(p)
    db.upsert_session(SessionRow(
        id=sid,
        robot_id="bot-1",
        started_at=started_at,
        ended_at=started_at + 1000,
        duration_ms=1000,
        label="anomaly:battery_low",
        mcap_path=mcap_path,
        mcap_size_bytes=size_bytes,
        topics=["/battery_state"],
        created_at=now_ms(),
        mcap_url=mcap_url,
        subsystem="power",
        summary=summary,
    ))
    return p


def test_env_int_tolerates_empty_and_garbage(monkeypatch):
    """Regression: compose passes `"${VAR:-}"` as an empty string, which
    crashed startup via int(""). _env_int must treat unset / empty /
    whitespace / non-numeric all as the default."""
    monkeypatch.delenv("MD_COLD_AFTER_DAYS", raising=False)
    assert _env_int("MD_COLD_AFTER_DAYS") == 0          # unset
    monkeypatch.setenv("MD_COLD_AFTER_DAYS", "")
    assert _env_int("MD_COLD_AFTER_DAYS") == 0          # empty (the crash case)
    monkeypatch.setenv("MD_COLD_AFTER_DAYS", "   ")
    assert _env_int("MD_COLD_AFTER_DAYS") == 0          # whitespace
    monkeypatch.setenv("MD_COLD_AFTER_DAYS", "not-a-number")
    assert _env_int("MD_COLD_AFTER_DAYS") == 0          # garbage -> default, no raise
    monkeypatch.setenv("MD_COLD_AFTER_DAYS", "30")
    assert _env_int("MD_COLD_AFTER_DAYS") == 30         # valid


def test_cold_releases_bytes_but_keeps_metadata(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    p = _mk_session(db, tmp_path, "old", started_at=now - 40 * _DAY_MS)

    result = sweep_lifecycle_once(
        db, cold_after_days=30, delete_after_days=0, now_ms_val=now
    )

    assert result.cooled_ids == ["old"]
    assert result.deleted_ids == []
    # File unlinked, byte pointers cleared, cold_at stamped.
    assert not p.exists()
    row = db.get_session("old")
    assert row is not None
    assert row.cold_at == now
    assert row.mcap_path == ""
    assert row.mcap_url is None
    # Incident metadata preserved — the whole point.
    assert row.summary == "battery_low on bot across /battery_state"
    assert row.subsystem == "power"


def test_cold_leaves_recent_sessions_alone(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    p = _mk_session(db, tmp_path, "fresh", started_at=now - 5 * _DAY_MS)

    result = sweep_lifecycle_once(
        db, cold_after_days=30, delete_after_days=0, now_ms_val=now
    )

    assert result.cooled_ids == []
    assert p.exists()
    assert db.get_session("fresh").cold_at is None


def test_cold_is_idempotent(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    _mk_session(db, tmp_path, "old", started_at=now - 40 * _DAY_MS)

    first = sweep_lifecycle_once(db, cold_after_days=30, delete_after_days=0, now_ms_val=now)
    second = sweep_lifecycle_once(db, cold_after_days=30, delete_after_days=0, now_ms_val=now)

    assert first.cooled_ids == ["old"]
    assert second.cooled_ids == []  # already cold — not re-processed


def test_cold_handles_hub_ingested_without_local_file(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    # Hub-ingested: bytes live on the robot (mcap_url), no local path.
    _mk_session(
        db, tmp_path, "remote",
        started_at=now - 40 * _DAY_MS,
        mcap_url="http://agent.local/mcap",
        write_file=False,
    )

    result = sweep_lifecycle_once(db, cold_after_days=30, delete_after_days=0, now_ms_val=now)

    assert result.cooled_ids == ["remote"]
    row = db.get_session("remote")
    assert row.cold_at == now
    assert row.mcap_url is None  # hub stops offering; robot's copy untouched (HR22)


def test_delete_purges_old_sessions(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    p = _mk_session(db, tmp_path, "ancient", started_at=now - 100 * _DAY_MS)

    result = sweep_lifecycle_once(
        db, cold_after_days=0, delete_after_days=90, now_ms_val=now
    )

    assert result.deleted_ids == ["ancient"]
    assert not p.exists()
    assert db.get_session("ancient") is None


def test_delete_takes_priority_over_cold(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    # Past BOTH thresholds — should be purged, not cooled-then-purged.
    _mk_session(db, tmp_path, "ancient", started_at=now - 100 * _DAY_MS)
    # Past cold only.
    _mk_session(db, tmp_path, "middle", started_at=now - 40 * _DAY_MS)

    result = sweep_lifecycle_once(
        db, cold_after_days=30, delete_after_days=90, now_ms_val=now
    )

    assert result.deleted_ids == ["ancient"]
    assert result.cooled_ids == ["middle"]
    assert db.get_session("ancient") is None
    assert db.get_session("middle").cold_at == now


def test_disabled_policies_are_noops(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    p = _mk_session(db, tmp_path, "old", started_at=now - 1000 * _DAY_MS)

    result = sweep_lifecycle_once(
        db, cold_after_days=0, delete_after_days=0, now_ms_val=now
    )

    assert result.cooled_ids == []
    assert result.deleted_ids == []
    assert p.exists()
    assert db.get_session("old") is not None


def test_cold_session_still_in_fleet_window(tmp_path):
    """A cooled session must still roll up in the dashboard — incident
    memory outlives the recording."""
    db = Db(tmp_path / "db.sqlite3")
    now = 100 * _DAY_MS
    _mk_session(db, tmp_path, "old", started_at=now - 40 * _DAY_MS)
    sweep_lifecycle_once(db, cold_after_days=30, delete_after_days=0, now_ms_val=now)

    rows = db.list_sessions_in_window(started_at_gte=0, started_at_lt=now + 1)
    assert [r.id for r in rows] == ["old"]


def test_admin_lifecycle_sweep_endpoint(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    # Build the app first so its Db is the one the endpoint uses, then seed.
    app = build_app(sessions, db_path, cold_after_days=30, delete_after_days=0)
    db = Db(db_path)
    # Far enough in the past that real wall-clock "now" still tiers it.
    _mk_session(db, sessions, "old", started_at=1)

    with TestClient(app) as client:
        r = client.post("/api/admin/lifecycle/sweep")
        assert r.status_code == 200
        body = r.json()
        assert body["cooled_ids"] == ["old"]
        assert body["cold_after_days"] == 30

        disk = client.get("/api/admin/disk").json()
        assert disk["cold_after_days"] == 30
        assert disk["lifecycle_enabled"] is True
