"""Backend should pick up new MCAP files written after startup."""

import time
from pathlib import Path

from fastapi.testclient import TestClient

import missiondebug_backend.main as main_mod
from missiondebug_agent.mcap_writer import write_session
from missiondebug_agent.ring_buffer import BufferedMessage


def _loader(_t: str) -> str:
    return "string data\n"


def _write(sessions_dir: Path, robot_id: str) -> None:
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
        sessions_dir / f"{robot_id}_{abs(hash(robot_id)) % 10**6}.mcap",
        robot_id=robot_id,
        topic_types={"/cmd_vel": "geometry_msgs/msg/Twist"},
        schema_loader=_loader,
    )


def test_periodic_rescan_picks_up_new_file(tmp_path: Path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _write(sessions, "robot-A")

    # Tight loop for fast test.
    monkeypatch.setattr(main_mod, "RESCAN_INTERVAL_S", 0.05)

    app = main_mod.build_app(sessions, tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        r = client.get("/api/sessions")
        assert {s["robot_id"] for s in r.json()["sessions"]} == {"robot-A"}

        # Drop a new file on disk after startup.
        _write(sessions, "robot-B")

        # Wait for the periodic task to pick it up. TestClient runs the
        # app's asyncio loop in a worker thread, so time.sleep here just
        # blocks the test thread without freezing the loop.
        ids: set[str] = set()
        for _ in range(40):  # up to 4s
            time.sleep(0.1)
            r = client.get("/api/sessions")
            ids = {s["robot_id"] for s in r.json()["sessions"]}
            if ids == {"robot-A", "robot-B"}:
                return
        raise AssertionError(f"Periodic rescan never indexed robot-B; saw {ids}")
