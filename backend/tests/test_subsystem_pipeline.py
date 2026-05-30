"""Regression: the agent embeds the configured `subsystem` in the MCAP
metadata, the backend's directory scan reads it onto the session row, and
the list endpoint can filter by it.

Before this fix, locally-saved (scanned) sessions had subsystem=NULL even
when the agent was configured with one. The filter rail groups robots by
their *agent heartbeat* subsystem, so a robot would appear under e.g.
"navigation" but its sessions carried no subsystem — clicking it produced a
dead-end empty list (subsystem=navigation AND robot_id=... matched nothing).
"""

from pathlib import Path

from fastapi.testclient import TestClient
from missiondebug_agent.mcap_writer import write_session
from missiondebug_agent.ring_buffer import BufferedMessage

from missiondebug_backend.main import build_app


def _loader(_t: str) -> str:
    return "string data\n"


def _write(sessions_dir: Path, robot_id: str, subsystem: str | None) -> None:
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
        sessions_dir / f"{robot_id}-{subsystem or 'none'}.mcap",
        robot_id=robot_id,
        topic_types={"/cmd_vel": "geometry_msgs/msg/Twist"},
        subsystem=subsystem,
        schema_loader=_loader,
    )


def test_subsystem_round_trip(tmp_path: Path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _write(sessions, "robot-A", "navigation")
    _write(sessions, "robot-B", None)  # no subsystem -> stays NULL

    app = build_app(sessions, tmp_path / "db.sqlite3")
    with TestClient(app) as client:
        body = client.get("/api/sessions").json()
        by_robot = {s["robot_id"]: s for s in body["sessions"]}
        # The configured subsystem rides through write -> scan -> row.
        assert by_robot["robot-A"]["subsystem"] == "navigation"
        # Empty string is normalized to NULL, not "".
        assert by_robot["robot-B"]["subsystem"] is None

        # And the same value is filterable — the combination the rail emits
        # (subsystem + robot_id) now actually matches.
        filtered = client.get(
            "/api/sessions?subsystem=navigation&robot_id=robot-A"
        ).json()
        assert {s["robot_id"] for s in filtered["sessions"]} == {"robot-A"}
