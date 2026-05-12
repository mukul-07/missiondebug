import sqlite3

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


# ---- v2 schema additions ----------------------------------------------


def test_session_supports_v2_columns(tmp_path):
    """SessionRow round-trips with mcap_url + subsystem set."""
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_session(SessionRow(
        id="hub-fetched-1", robot_id="r1",
        started_at=1, ended_at=2, duration_ms=1, label="anomaly:stall",
        mcap_path="/local/unused.mcap", mcap_size_bytes=10,
        topics=["/tf"], created_at=1,
        mcap_url="http://robot-001.local:7000/api/sessions/x/mcap",
        subsystem="navigation",
    ))
    rows = db.list_sessions()
    assert len(rows) == 1
    assert rows[0].mcap_url == "http://robot-001.local:7000/api/sessions/x/mcap"
    assert rows[0].subsystem == "navigation"


def test_session_v2_columns_default_to_none(tmp_path):
    """v1.5-style callers (no mcap_url/subsystem) still work."""
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_session(SessionRow(
        id="legacy", robot_id="r1",
        started_at=1, ended_at=2, duration_ms=1, label=None,
        mcap_path="/local/x.mcap", mcap_size_bytes=10, topics=[],
        created_at=1,
    ))
    got = db.get_session("legacy")
    assert got is not None
    assert got.mcap_url is None
    assert got.subsystem is None


def test_migration_adds_v2_columns_to_existing_db(tmp_path):
    """A pre-v2 sqlite file (no mcap_url/subsystem) gets the columns
    auto-added on Db init. No data loss."""
    db_path = tmp_path / "old.sqlite3"
    # Hand-craft a v1.5-shaped sessions table — no v2 columns.
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
              id TEXT PRIMARY KEY,
              robot_id TEXT NOT NULL,
              started_at INTEGER NOT NULL,
              ended_at INTEGER NOT NULL,
              duration_ms INTEGER NOT NULL,
              label TEXT,
              mcap_path TEXT NOT NULL,
              mcap_size_bytes INTEGER NOT NULL,
              topics_json TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            INSERT INTO sessions VALUES
              ('old-1', 'r1', 100, 200, 100, 'pre-v2',
               '/old/x.mcap', 0, '[]', 100);
        """)
        conn.commit()
    # Now open with the v2 Db — should migrate without dropping the row.
    db = Db(db_path)
    rows = db.list_sessions()
    assert len(rows) == 1
    assert rows[0].id == "old-1"
    assert rows[0].mcap_url is None
    assert rows[0].subsystem is None


# ---- v2 agents + heartbeats -------------------------------------------


def test_upsert_agent_first_seen_is_stable(tmp_path):
    """first_seen is set on initial insert and never updated."""
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_agent(robot_id="r1", agent_url="http://r1:7000", agent_version="1.5.0")
    a1 = db.get_agent("r1")
    assert a1 is not None
    assert a1.first_seen > 0
    assert a1.agent_version == "1.5.0"
    first = a1.first_seen

    # Second upsert: agent_version changes, first_seen stays put.
    db.upsert_agent(robot_id="r1", agent_version="1.5.1")
    a2 = db.get_agent("r1")
    assert a2 is not None
    assert a2.first_seen == first
    assert a2.agent_version == "1.5.1"
    # agent_url not supplied this time — stays as it was.
    assert a2.agent_url == "http://r1:7000"


def test_record_heartbeat_updates_last_seen_and_inserts_history(tmp_path):
    db = Db(tmp_path / "x.sqlite3")
    db.record_heartbeat(robot_id="r1", buffer_size=42, agent_url="http://r1:7000")
    a = db.get_agent("r1")
    assert a is not None
    assert a.last_heartbeat is not None
    assert a.agent_url == "http://r1:7000"

    db.record_heartbeat(robot_id="r1", buffer_size=43)
    a = db.get_agent("r1")
    assert a is not None
    second_hb = a.last_heartbeat
    # Heartbeat advanced.
    assert second_hb is not None

    # History exists; count by direct query.
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM agent_heartbeats").fetchone()[0]
    assert n == 2


def test_list_agents_returns_all(tmp_path):
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_agent(robot_id="r1")
    db.upsert_agent(robot_id="r2")
    db.upsert_agent(robot_id="r3")
    agents = db.list_agents()
    assert [a.robot_id for a in agents] == ["r1", "r2", "r3"]


def test_prune_old_heartbeats(tmp_path):
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_agent(robot_id="r1")
    # Hand-insert a couple of stale heartbeats + one recent.
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO agent_heartbeats (robot_id, heartbeat_at, buffer_size) VALUES (?, ?, ?)",
            ("r1", 1000, 1),
        )
        conn.execute(
            "INSERT INTO agent_heartbeats (robot_id, heartbeat_at, buffer_size) VALUES (?, ?, ?)",
            ("r1", 2000, 2),
        )
        conn.execute(
            "INSERT INTO agent_heartbeats (robot_id, heartbeat_at, buffer_size) VALUES (?, ?, ?)",
            ("r1", 9_999_999_999, 3),
        )
        conn.commit()
    deleted = db.prune_old_heartbeats(before_ms=5000)
    assert deleted == 2
    with db.connect() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM agent_heartbeats").fetchone()[0]
    assert remaining == 1


def test_agent_heartbeat_cascade_delete(tmp_path):
    """Deleting an agent removes its heartbeats (FK cascade)."""
    db = Db(tmp_path / "x.sqlite3")
    db.record_heartbeat(robot_id="r1", buffer_size=1)
    db.record_heartbeat(robot_id="r1", buffer_size=2)
    with db.connect() as conn:
        conn.execute("DELETE FROM agents WHERE robot_id = ?", ("r1",))
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM agent_heartbeats WHERE robot_id = ?", ("r1",)
        ).fetchone()[0]
    assert n == 0
