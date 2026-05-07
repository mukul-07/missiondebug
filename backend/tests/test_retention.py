"""Phase 4 — disk retention sweeper tests."""

from __future__ import annotations

from pathlib import Path

from missiondebug_backend.db import Db, SessionRow, now_ms
from missiondebug_backend.retention import sweep_once


def _mk_session(
    db: Db,
    sessions_dir: Path,
    sid: str,
    started_at: int,
    size_bytes: int,
) -> Path:
    """Create both an MCAP file on disk and a row in the DB."""
    p = sessions_dir / f"{sid}.mcap"
    p.write_bytes(b"\x00" * size_bytes)
    db.upsert_session(SessionRow(
        id=sid,
        robot_id="r",
        started_at=started_at,
        ended_at=started_at + 1000,
        duration_ms=1000,
        label=None,
        mcap_path=str(p),
        mcap_size_bytes=size_bytes,
        topics=[],
        created_at=now_ms(),
    ))
    return p


def test_sweep_noop_when_under_cap(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    _mk_session(db, tmp_path, "a", started_at=1000, size_bytes=100)

    result = sweep_once(db, cap_bytes=10_000)

    assert result.deleted_ids == []
    assert result.bytes_freed == 0
    assert db.total_mcap_bytes() == 100


def test_sweep_disabled_when_cap_zero(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    _mk_session(db, tmp_path, "a", started_at=1000, size_bytes=100)

    result = sweep_once(db, cap_bytes=0)

    assert result.deleted_ids == []
    assert db.total_mcap_bytes() == 100


def test_sweep_deletes_oldest_until_under_cap(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    p_old = _mk_session(db, tmp_path, "old",    started_at=1000, size_bytes=100)
    p_mid = _mk_session(db, tmp_path, "mid",    started_at=2000, size_bytes=100)
    p_new = _mk_session(db, tmp_path, "newest", started_at=3000, size_bytes=100)

    # Total = 300; cap at 150 → must drop 2 oldest.
    result = sweep_once(db, cap_bytes=150)

    assert result.deleted_ids == ["old", "mid"]
    assert result.bytes_freed == 200
    assert db.total_mcap_bytes() == 100

    # Files for deleted sessions are gone, newest survives.
    assert not p_old.exists()
    assert not p_mid.exists()
    assert p_new.exists()

    # Surviving DB row.
    remaining = db.list_sessions()
    assert len(remaining) == 1
    assert remaining[0].id == "newest"


def test_sweep_cascade_deletes_annotations(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    _mk_session(db, tmp_path, "old", started_at=1000, size_bytes=100)
    _mk_session(db, tmp_path, "new", started_at=2000, size_bytes=100)
    db.insert_annotation("old", time_ns=42, body="will-die")
    db.insert_annotation("new", time_ns=43, body="survives")

    sweep_once(db, cap_bytes=100)

    # Old session and its annotation gone; new one's annotation intact.
    assert db.list_annotations("old") == []
    assert len(db.list_annotations("new")) == 1


def test_sweep_tolerates_missing_files(tmp_path):
    """If an MCAP is gone (e.g. user deleted it manually), sweep still
    drops the orphan DB row instead of looping."""
    db = Db(tmp_path / "db.sqlite3")
    p = _mk_session(db, tmp_path, "a", started_at=1000, size_bytes=200)
    p.unlink()  # disappear the file before sweep

    result = sweep_once(db, cap_bytes=50)

    assert "a" in result.deleted_ids
    assert db.total_mcap_bytes() == 0
