"""End-to-end: agent writes MCAP -> backend scans -> backend serves -> range works."""

from pathlib import Path

from fastapi.testclient import TestClient

from missiondebug_agent.mcap_writer import write_session
from missiondebug_agent.ring_buffer import BufferedMessage
from missiondebug_backend.main import build_app


def _loader(_t: str) -> str:
    return "string data\n"


def test_full_pipeline(tmp_path: Path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    items = [
        BufferedMessage(
            timestamp_ns=i * 100_000_000,
            wall_ns=1_700_000_000_000_000_000 + i * 100_000_000,
            topic="/cmd_vel",
            payload=b"\x00\x00\x00\x00",
        )
        for i in range(10)
    ]
    write_session(
        items,
        sessions / "robot-001_e2e.mcap",
        robot_id="robot-001",
        topic_types={"/cmd_vel": "geometry_msgs/msg/Twist"},
        schema_loader=_loader,
    )

    app = build_app(sessions, tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/sessions")
        assert r.status_code == 200
        sessions_list = r.json()["sessions"]
        assert len(sessions_list) == 1
        sid = sessions_list[0]["id"]
        assert sid == "robot-001_e2e"
        assert "/cmd_vel" in sessions_list[0]["topics"]

        # Detail
        r = client.get(f"/api/sessions/{sid}")
        assert r.status_code == 200

        # Full file
        r = client.get(f"/api/sessions/{sid}/mcap")
        assert r.status_code == 200
        assert r.content[:8].startswith(b"\x89MCAP")  # MCAP magic prefix

        # Range
        r = client.get(f"/api/sessions/{sid}/mcap", headers={"range": "bytes=0-7"})
        assert r.status_code == 206
        assert len(r.content) == 8
