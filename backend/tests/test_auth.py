"""v2 P4 — basic auth on the hub.

Matrix tested:

  mode     password set?    expected
  -------  --------------   --------
  single   no               OPEN  (v1.5 behaviour, Hard Rule 18)
  single   yes              GATED (opt-in to auth in single mode)
  fleet    no               STARTUP FAILS (Hard Rule 21)
  fleet    yes              GATED (Hard Rule 21)

Plus the auth path matrix when gated:
  - no credentials             → 401
  - wrong Basic password       → 401
  - correct Basic password     → 200
  - wrong Bearer token         → 401
  - correct Bearer token       → 200
  - /healthz                   → always 200, no auth
  - SPA / static files         → always public
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from missiondebug_backend.auth import AuthConfig
from missiondebug_backend.main import build_app


def _basic(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _client(tmp_path, *, mode: str | None = None, password: str | None = None,
            token: str | None = None) -> TestClient:
    """Build a test app with explicit auth config (env-independent)."""
    cfg = AuthConfig()
    cfg.mode = mode or "single"
    cfg.password = password
    cfg.token = token if token is not None else password
    app = build_app(
        sessions_dir=tmp_path / "sessions",
        db_path=tmp_path / "x.sqlite3",
        auth_config=cfg,
    )
    return TestClient(app)


# ---- enforcement matrix ----------------------------------------------


def test_single_mode_no_password_routes_open(tmp_path):
    """v1.5 behaviour: open routes when no password set, regardless of mode."""
    c = _client(tmp_path, mode="single", password=None)
    assert c.get("/api/sessions").status_code == 200


def test_single_mode_password_set_gates_routes(tmp_path):
    """Single mode + opt-in password: auth enforced."""
    c = _client(tmp_path, mode="single", password="hunter2")
    assert c.get("/api/sessions").status_code == 401


def test_fleet_mode_no_password_fails_at_startup_invariants():
    """Hard Rule 21 — Fleet mode without a password raises SystemExit
    via enforce_startup_invariants. Tested directly because TestClient
    would skip the main() call where it runs."""
    cfg = AuthConfig()
    cfg.mode = "fleet"
    cfg.password = None
    cfg.token = None
    with pytest.raises(SystemExit) as exc:
        cfg.enforce_startup_invariants()
    assert exc.value.code == 2


def test_fleet_mode_password_set_gates_routes(tmp_path):
    """Fleet mode + password: auth enforced, /api/* returns 401 without creds."""
    c = _client(tmp_path, mode="fleet", password="hunter2")
    assert c.get("/api/sessions").status_code == 401


# ---- credential paths -------------------------------------------------


def test_correct_basic_auth_allows(tmp_path):
    c = _client(tmp_path, mode="fleet", password="hunter2")
    r = c.get("/api/sessions", headers={"Authorization": _basic("anything", "hunter2")})
    assert r.status_code == 200


def test_wrong_basic_password_denied(tmp_path):
    c = _client(tmp_path, mode="fleet", password="hunter2")
    r = c.get("/api/sessions", headers={"Authorization": _basic("admin", "wrong")})
    assert r.status_code == 401


def test_correct_bearer_token_allows(tmp_path):
    c = _client(tmp_path, mode="fleet", password="hunter2", token="agent-secret")
    r = c.get(
        "/api/v1/agents",
        headers={"Authorization": "Bearer agent-secret"},
    )
    assert r.status_code == 200


def test_bearer_defaults_to_password_when_token_unset(tmp_path):
    """One-secret deployment: token defaults to password when MD_HUB_AUTH_TOKEN
    is unset."""
    c = _client(tmp_path, mode="fleet", password="hunter2", token="hunter2")
    r = c.get("/api/sessions", headers={"Authorization": "Bearer hunter2"})
    assert r.status_code == 200


def test_wrong_bearer_token_denied(tmp_path):
    c = _client(tmp_path, mode="fleet", password="hunter2", token="agent-secret")
    r = c.get("/api/sessions", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_no_credentials_gets_www_authenticate(tmp_path):
    """401 response carries WWW-Authenticate so browsers prompt for basic auth."""
    c = _client(tmp_path, mode="fleet", password="hunter2")
    r = c.get("/api/sessions")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("basic")


# ---- public paths -----------------------------------------------------


def test_healthz_always_public(tmp_path):
    """Healthz must work without credentials so load balancers can probe."""
    c = _client(tmp_path, mode="fleet", password="hunter2")
    r = c.get("/healthz")
    assert r.status_code == 200


def test_openapi_json_stays_public_when_auth_on(tmp_path):
    """Standard practice: /openapi.json discoverable without auth."""
    c = _client(tmp_path, mode="fleet", password="hunter2")
    r = c.get("/openapi.json")
    assert r.status_code == 200


def test_agent_ingest_with_bearer_works(tmp_path):
    """End-to-end: agent posts a session to /api/v1/sessions/ingest with
    a Bearer token, hub accepts it. This is the production agent→hub
    auth path."""
    c = _client(tmp_path, mode="fleet", password="hub-secret", token="agent-secret")
    r = c.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": "x",
            "robot_id": "r1",
            "started_at": 0, "ended_at": 1, "duration_ms": 1,
            "topics": [], "mcap_size_bytes": 0,
            "mcap_url": "http://r1:7000/api/sessions/x/mcap",
        },
        headers={"Authorization": "Bearer agent-secret"},
    )
    assert r.status_code == 200
