from missiondebug_backend.db import Db, SessionRow, now_ms


def test_upsert_and_list(tmp_path):
    db = Db(tmp_path / "x.sqlite3")
    row = SessionRow(
        id="robot-001_20260101T000000Z",
        robot_id="robot-001",
        started_at=1000,
        ended_at=2000,
        duration_ms=1000,
        label=None,
        mcap_path="/tmp/x.mcap",
        mcap_size_bytes=42,
        topics=["/tf", "/cmd_vel"],
        created_at=now_ms(),
    )
    db.upsert_session(row)
    rows = db.list_sessions()
    assert len(rows) == 1
    assert rows[0].topics == ["/tf", "/cmd_vel"]

    got = db.get_session(row.id)
    assert got is not None and got.id == row.id


def test_known_paths(tmp_path):
    db = Db(tmp_path / "x.sqlite3")
    assert db.known_paths() == set()
    db.upsert_session(SessionRow(
        id="a", robot_id="r", started_at=0, ended_at=0, duration_ms=0,
        label=None, mcap_path="/p/a.mcap", mcap_size_bytes=0, topics=[],
        created_at=0,
    ))
    assert db.known_paths() == {"/p/a.mcap"}
