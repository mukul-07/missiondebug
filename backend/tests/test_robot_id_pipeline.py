"""Phase 2: agent writes robot_id to MCAP metadata, backend reads it,
list endpoint exposes per-robot filter."""

from pathlib import Path

from fastapi.testclient import TestClient

from missiondebug_agent.mcap_writer import write_session
from missiondebug_agent.ring_buffer import BufferedMessage
from missiondebug_backend.main import build_app


def _loader(_t: str) -> str:
    return "string data\n"


def _write(sessions_dir: Path, robot_id: str, label: str | None = None) -> None:
    items = [
        BufferedMessage(
            timestamp_ns=i * 100_000_000,
            wall_ns=1_700_000_000_000_000_000 + i * 100_000_000,
            topic="/cmd_vel",
            payload=b"\x00" * 4,
        )
        for i in range(10)
    ]
    # Filename intentionally does NOT contain the robot_id, to prove the
    # backend reads it from MCAP metadata, not the filename.
    write_session(
        items,
        sessions_dir / f"untracked-name-{robot_id}.mcap",
        robot_id=robot_id,
        topic_types={"/cmd_vel": "geometry_msgs/msg/Twist"},
        label=label,
        schema_loader=_loader,
    )


def test_robot_id_round_trip(tmp_path: Path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _write(sessions, "robot-A", label="anomaly:stall")
    _write(sessions, "robot-B")

    app = build_app(sessions, tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/sessions")
        assert r.status_code == 200
        body = r.json()
        ids = {s["robot_id"] for s in body["sessions"]}
        assert ids == {"robot-A", "robot-B"}
        assert set(body["robots"]) == {"robot-A", "robot-B"}

        # Filter
        r = client.get("/api/sessions?robot_id=robot-A")
        body = r.json()
        assert {s["robot_id"] for s in body["sessions"]} == {"robot-A"}
        assert body["sessions"][0]["label"] == "anomaly:stall"
