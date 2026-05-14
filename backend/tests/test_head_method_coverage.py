"""HEAD method coverage check.

Read-only API routes should answer HEAD as well as GET. Failing on HEAD
silently breaks: curl -I, browser link previews, HTTP load balancer
probes, and any agent code that probes endpoints for liveness.

This file is the safety net — if a new GET route lands without HEAD,
the relevant test here fails until the route declaration is widened
(usually via @router.api_route(..., methods=["GET", "HEAD"])).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(
        build_app(
            sessions_dir=tmp_path / "sessions",
            db_path=tmp_path / "x.sqlite3",
        )
    )


# (path, expected_status) — pure GET-shaped endpoints we want HEAD to
# also satisfy. Anything that takes a session_id needs one ingested
# first (see test_head_on_session_routes).
PUBLIC_HEAD_TARGETS = [
    "/healthz",
    "/api/sessions",
    "/api/admin/disk",
    "/api/v1/agents",
    "/api/v1/agents/health",
]


@pytest.mark.parametrize("path", PUBLIC_HEAD_TARGETS)
def test_head_returns_2xx(client, path):
    """HEAD on each GET route returns 2xx, never 404. Body MUST be empty."""
    r = client.head(path)
    assert r.status_code < 400, (
        f"HEAD {path} returned {r.status_code} — likely a @router.get without "
        f"methods=['GET','HEAD']. See routes/* for how api_route is used."
    )
    assert r.content == b"", f"HEAD {path} returned a body — Starlette should strip it"


def test_head_on_session_routes(client):
    """Session-specific routes (require ingest first). Asserts HEAD works
    on the single-session detail + annotations list."""
    client.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": "x", "robot_id": "r",
            "started_at": 0, "ended_at": 1, "duration_ms": 1,
            "topics": [], "mcap_size_bytes": 0,
            "mcap_url": "http://r:7000/api/sessions/x/mcap",
        },
    )
    # Detail endpoint
    r = client.head("/api/sessions/x")
    assert r.status_code == 200
    assert r.content == b""
    # Annotations list
    r = client.head("/api/sessions/x/annotations")
    assert r.status_code == 200
    assert r.content == b""
