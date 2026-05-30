"""Regression: the directory scanner must not clobber a hub-ingested
session's rich fields (summary, subsystem, mcap_url).

In the dev / single-machine setup the backend's --sessions-dir is the
agent's output dir, so the same session arrives twice: once via hub
ingest (summary + subsystem, but mcap_path="") and once as a local MCAP
file the scanner sees. The scanner dedups by *path*; the ingested row's
path is empty, so the scanner re-inserts the same id and INSERT OR
REPLACE wipes the summary. Symptom: summary=null in the DB even though
the agent sent it and the ingest endpoint accepted it.

The scanner should instead recognise the existing row and only attach
the local mcap_path so the file becomes servable, preserving everything
the hub already knows.
"""

from pathlib import Path

from missiondebug_agent.mcap_writer import write_session
from missiondebug_agent.ring_buffer import BufferedMessage

from missiondebug_backend.db import Db, SessionRow, now_ms
from missiondebug_backend.scanner import scan_directory


def _write_mcap(path: Path, robot_id: str) -> None:
    items = [
        BufferedMessage(
            timestamp_ns=i * 100_000_000,
            wall_ns=1_700_000_000_000_000_000 + i * 100_000_000,
            topic="/cmd_vel",
            payload=b"\x00" * 4,
        )
        for i in range(10)
    ]
    write_session(
        items,
        path,
        robot_id=robot_id,
        topic_types={"/cmd_vel": "geometry_msgs/msg/Twist"},
        schema_loader=lambda _t: "string data\n",
    )


def test_scan_preserves_hub_ingested_summary(tmp_path: Path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db = Db(tmp_path / "db.sqlite3")

    # Agent wrote the MCAP locally; the scanner will see this file.
    mcap = sessions / "warehouse-bot-03_T1.mcap"
    _write_mcap(mcap, "warehouse-bot-03")
    sid = mcap.stem

    # Simulate the hub ingest that already landed: rich row, empty path.
    db.upsert_session(
        SessionRow(
            id=sid,
            robot_id="warehouse-bot-03",
            started_at=1_700_000_000_000,
            ended_at=1_700_000_060_000,
            duration_ms=60_000,
            label="anomaly:battery_low",
            mcap_path="",
            mcap_size_bytes=243_712,
            topics=["/cmd_vel"],
            created_at=now_ms(),
            mcap_url="http://agent.local/api/sessions/x/mcap",
            subsystem="power",
            summary="Auto-triggered by rule 'battery_low' ... Total payload: 238.0 KB.",
        )
    )

    # The backend rescans its sessions dir (dev setup: same dir).
    scan_directory(sessions, db)

    row = db.get_session(sid)
    assert row is not None
    # The hub-provided fields must survive the scan.
    assert row.summary == "Auto-triggered by rule 'battery_low' ... Total payload: 238.0 KB."
    assert row.subsystem == "power"
    assert row.mcap_url == "http://agent.local/api/sessions/x/mcap"
    # And the local file is now attached so it's servable.
    assert row.mcap_path == str(mcap.resolve())
