from pathlib import Path

from fastapi.testclient import TestClient

from missiondebug_backend.db import SessionRow, now_ms
from missiondebug_backend.main import build_app


def _setup(tmp_path: Path) -> tuple[TestClient, Path]:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    payload = bytes(range(256)) * 32  # 8192 bytes, deterministic
    f = sessions_dir / "robot-001_test.mcap"
    f.write_bytes(payload)

    app = build_app(sessions_dir, tmp_path / "db.sqlite3")
    # Insert a row pointing at the file directly (skip MCAP metadata extraction).
    from missiondebug_backend.db import Db
    db = Db(tmp_path / "db.sqlite3")
    db.upsert_session(SessionRow(
        id="robot-001_test",
        robot_id="robot-001",
        started_at=0, ended_at=0, duration_ms=0, label=None,
        mcap_path=str(f.resolve()),
        mcap_size_bytes=len(payload),
        topics=[], created_at=now_ms(),
    ))
    return TestClient(app), f


def test_full_download(tmp_path):
    client, f = _setup(tmp_path)
    r = client.get("/api/sessions/robot-001_test/mcap")
    assert r.status_code == 200
    assert r.content == f.read_bytes()
    assert r.headers["accept-ranges"] == "bytes"


def test_range_request(tmp_path):
    client, f = _setup(tmp_path)
    full = f.read_bytes()
    r = client.get(
        "/api/sessions/robot-001_test/mcap",
        headers={"range": "bytes=10-19"},
    )
    assert r.status_code == 206
    assert r.content == full[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(full)}"


def test_suffix_range(tmp_path):
    client, f = _setup(tmp_path)
    full = f.read_bytes()
    r = client.get(
        "/api/sessions/robot-001_test/mcap",
        headers={"range": "bytes=-100"},
    )
    assert r.status_code == 206
    assert r.content == full[-100:]


def test_invalid_range(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.get(
        "/api/sessions/robot-001_test/mcap",
        headers={"range": "bytes=999999-999999"},
    )
    assert r.status_code == 416


def test_404_for_unknown_session(tmp_path):
    client, _ = _setup(tmp_path)
    r = client.get("/api/sessions/nope/mcap")
    assert r.status_code == 404
