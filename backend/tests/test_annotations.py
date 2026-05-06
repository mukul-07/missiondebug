"""Phase 3: annotations CRUD + appears in session list with count."""

from pathlib import Path

from fastapi.testclient import TestClient

from missiondebug_agent.mcap_writer import write_session
from missiondebug_agent.ring_buffer import BufferedMessage
from missiondebug_backend.main import build_app


def _loader(_t: str) -> str:
    return "string data\n"


def _seed_session(sessions_dir: Path, robot_id: str = "robot-001") -> str:
    items = [
        BufferedMessage(
            timestamp_ns=i * 100_000_000,
            wall_ns=1_700_000_000_000_000_000 + i * 100_000_000,
            topic="/cmd_vel",
            payload=b"\x00" * 4,
        )
        for i in range(10)
    ]
    out = sessions_dir / f"{robot_id}_anno.mcap"
    write_session(
        items, out,
        robot_id=robot_id,
        topic_types={"/cmd_vel": "geometry_msgs/msg/Twist"},
        schema_loader=_loader,
    )
    return out.stem


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    sid = _seed_session(sessions)
    app = build_app(sessions, tmp_path / "db.sqlite3")
    return TestClient(app), sid


def test_annotation_lifecycle(tmp_path: Path):
    client, sid = _client(tmp_path)
    with client:
        # initially empty
        r = client.get(f"/api/sessions/{sid}/annotations")
        assert r.status_code == 200
        assert r.json() == {"annotations": []}

        # create one
        r = client.post(
            f"/api/sessions/{sid}/annotations",
            json={"time_ns": 12_500_000_000, "body": "  forklift in lane  "},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["body"] == "forklift in lane"  # trimmed
        assert body["session_id"] == sid
        assert body["time_ns"] == 12_500_000_000
        anno_id = body["id"]

        # list returns it
        r = client.get(f"/api/sessions/{sid}/annotations")
        assert len(r.json()["annotations"]) == 1

        # session detail + list reflect count
        r = client.get(f"/api/sessions/{sid}")
        assert r.json()["annotation_count"] == 1
        r = client.get("/api/sessions")
        assert r.json()["sessions"][0]["annotation_count"] == 1

        # delete
        r = client.delete(f"/api/annotations/{anno_id}")
        assert r.status_code == 204
        r = client.get(f"/api/sessions/{sid}/annotations")
        assert r.json() == {"annotations": []}

        # delete missing -> 404
        r = client.delete(f"/api/annotations/{anno_id}")
        assert r.status_code == 404


def test_update(tmp_path: Path):
    client, sid = _client(tmp_path)
    with client:
        r = client.post(
            f"/api/sessions/{sid}/annotations",
            json={"time_ns": 1_000_000_000, "body": "first"},
        )
        anno_id = r.json()["id"]
        original_created_at = r.json()["created_at"]

        # update body
        r = client.put(
            f"/api/annotations/{anno_id}",
            json={"body": "  updated text  "},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["body"] == "updated text"  # trimmed
        assert body["id"] == anno_id
        assert body["time_ns"] == 1_000_000_000  # unchanged
        assert body["created_at"] == original_created_at  # unchanged

        # update missing -> 404
        r = client.put("/api/annotations/99999", json={"body": "x"})
        assert r.status_code == 404

        # empty body rejected
        r = client.put(f"/api/annotations/{anno_id}", json={"body": ""})
        assert r.status_code == 422


def test_validation(tmp_path: Path):
    client, sid = _client(tmp_path)
    with client:
        # empty body rejected
        r = client.post(
            f"/api/sessions/{sid}/annotations",
            json={"time_ns": 0, "body": ""},
        )
        assert r.status_code == 422
        # negative time rejected
        r = client.post(
            f"/api/sessions/{sid}/annotations",
            json={"time_ns": -1, "body": "x"},
        )
        assert r.status_code == 422
        # unknown session
        r = client.post(
            "/api/sessions/nope/annotations",
            json={"time_ns": 0, "body": "x"},
        )
        assert r.status_code == 404
