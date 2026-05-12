"""v2 P1.5: hub-as-proxy for MCAP fetch.

When a session has mcap_url set to an HTTP URL, the hub streams the
response from that URL straight through to the client, forwarding the
Range header. The hub never buffers the MCAP bytes — Hard Rule 19.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Thread
from wsgiref.simple_server import make_server

from fastapi.testclient import TestClient

from missiondebug_backend.db import SessionRow, now_ms
from missiondebug_backend.main import build_app


# ---- fixture: a tiny "agent" that serves a known MCAP-like blob ------


_FAKE_MCAP = b"FAKE_MCAP_PAYLOAD" * 1000  # 17 KB


def _fake_agent_app(environ, start_response):
    """Minimal WSGI app pretending to be an agent's mcap endpoint."""
    path = environ.get("PATH_INFO", "")
    if not path.endswith("/mcap"):
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]
    rng = environ.get("HTTP_RANGE")
    size = len(_FAKE_MCAP)
    if rng is None:
        start_response("200 OK", [
            ("Content-Type", "application/octet-stream"),
            ("Accept-Ranges", "bytes"),
            ("Content-Length", str(size)),
        ])
        return [_FAKE_MCAP]
    # Parse "bytes=START-END"
    assert rng.startswith("bytes=")
    span = rng[len("bytes="):]
    start_s, end_s = span.split("-")
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else size - 1
    chunk = _FAKE_MCAP[start : end + 1]
    start_response("206 Partial Content", [
        ("Content-Type", "application/octet-stream"),
        ("Accept-Ranges", "bytes"),
        ("Content-Range", f"bytes {start}-{end}/{size}"),
        ("Content-Length", str(len(chunk))),
    ])
    return [chunk]


@contextmanager
def _running_fake_agent():
    server = make_server("127.0.0.1", 0, _fake_agent_app)
    port = server.server_address[1]
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


# ---- helpers ----------------------------------------------------------


def _client_with_hub_session(tmp_path, mcap_url: str) -> TestClient:
    app = build_app(
        sessions_dir=tmp_path / "sessions",
        db_path=tmp_path / "x.sqlite3",
    )
    # Stash a session that points at the fake agent.
    # Reach into the deps via build_app's get_db closure indirectly by
    # going through the ingest endpoint (cleaner than monkeypatching).
    c = TestClient(app)
    c.post("/api/v1/sessions/ingest", json={
        "session_id": "hub-1",
        "robot_id": "robot-001",
        "started_at": 0, "ended_at": 1000, "duration_ms": 1000,
        "label": "test", "topics": [], "mcap_size_bytes": len(_FAKE_MCAP),
        "mcap_url": mcap_url,
    })
    return c


# ---- tests ------------------------------------------------------------


def test_proxy_returns_full_body(tmp_path):
    """No Range header: hub proxies the full MCAP through."""
    with _running_fake_agent() as base:
        c = _client_with_hub_session(tmp_path, f"{base}/api/sessions/hub-1/mcap")
        r = c.get("/api/sessions/hub-1/mcap")
    assert r.status_code == 200
    assert r.content == _FAKE_MCAP
    assert r.headers.get("Content-Length") == str(len(_FAKE_MCAP))


def test_proxy_forwards_range(tmp_path):
    """Range header is forwarded; the proxy returns 206 + Content-Range."""
    with _running_fake_agent() as base:
        c = _client_with_hub_session(tmp_path, f"{base}/api/sessions/hub-1/mcap")
        r = c.get("/api/sessions/hub-1/mcap", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.content == _FAKE_MCAP[:100]
    assert r.headers["Content-Range"] == f"bytes 0-99/{len(_FAKE_MCAP)}"


def test_proxy_returns_502_when_agent_unreachable(tmp_path):
    """If the agent is down, hub returns 502."""
    c = _client_with_hub_session(
        tmp_path,
        # Port 1 is unbindable on most systems → connection refused.
        "http://127.0.0.1:1/api/sessions/hub-1/mcap",
    )
    r = c.get("/api/sessions/hub-1/mcap")
    assert r.status_code == 502


def test_local_path_still_works(tmp_path):
    """v1.5 single-robot path: no mcap_url, uses mcap_path. Hard Rule 18."""
    # Write a fake MCAP-shaped file directly to disk.
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    mcap_file = sessions_dir / "local.mcap"
    mcap_file.write_bytes(_FAKE_MCAP)

    app = build_app(
        sessions_dir=sessions_dir,
        db_path=tmp_path / "x.sqlite3",
    )
    # Use the public ingest path is wrong here (it sets mcap_url). Drop
    # straight into the db via the app's dependency.
    from missiondebug_backend.db import Db
    db = Db(tmp_path / "x.sqlite3")
    db.upsert_session(SessionRow(
        id="local-1", robot_id="r1",
        started_at=0, ended_at=1, duration_ms=1, label=None,
        mcap_path=str(mcap_file), mcap_size_bytes=len(_FAKE_MCAP),
        topics=[], created_at=now_ms(),
    ))
    c = TestClient(app)
    r = c.get("/api/sessions/local-1/mcap", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.content == _FAKE_MCAP[:10]


def test_proxy_returns_501_for_s3_scheme(tmp_path):
    """s3:// is documented as v2 Phase 5; should be a clear error today."""
    c = _client_with_hub_session(
        tmp_path,
        "s3://bucket/sessions/hub-1.mcap",
    )
    r = c.get("/api/sessions/hub-1/mcap")
    assert r.status_code == 501
