"""MD_CORS_ORIGINS: operators add browser origins (e.g. the Foxglove app so
the MissionDebug panel extension can call this API); unset keeps the dev-only
default. CORS governs which origins may ask; auth stays the access control.
"""

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app

FOXGLOVE = "https://app.foxglove.dev"


def _app(tmp_path):
    return build_app(tmp_path / "s", tmp_path / "db.sqlite3")


def test_default_allows_dev_origin_only(tmp_path, monkeypatch):
    monkeypatch.delenv("MD_CORS_ORIGINS", raising=False)
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/sessions", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
        r = c.get("/api/sessions", headers={"Origin": FOXGLOVE})
        assert "access-control-allow-origin" not in r.headers


def test_env_adds_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("MD_CORS_ORIGINS", f"{FOXGLOVE}, http://hub.internal:8000")
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/sessions", headers={"Origin": FOXGLOVE})
        assert r.headers.get("access-control-allow-origin") == FOXGLOVE
        # unknown origin still refused
        r = c.get("/api/sessions", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


def test_empty_env_means_default(tmp_path, monkeypatch):
    # compose passes "${VAR:-}" as an empty string — must behave like unset
    monkeypatch.setenv("MD_CORS_ORIGINS", "")
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/sessions", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_preflight_allows_put(tmp_path, monkeypatch):
    """Browsers preflight cross-origin PUT (OPTIONS + request-method). The
    Foxglove panel's inline resolve PUTs the resolution endpoint, so PUT
    must be in the CORS allow-methods — GET/POST alone 400s the preflight
    and the panel write silently fails."""
    monkeypatch.setenv("MD_CORS_ORIGINS", FOXGLOVE)
    with TestClient(_app(tmp_path)) as c:
        r = c.options(
            "/api/v2/sessions/SES-1/resolution",
            headers={
                "Origin": FOXGLOVE,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r.status_code == 200
        assert "PUT" in r.headers.get("access-control-allow-methods", "")


def test_auth_401_carries_cors_headers(tmp_path, monkeypatch):
    """CORS must wrap OUTSIDE the auth middleware: an auth-enabled hub's
    401 needs the allow-origin header, or a cross-origin caller (the
    Foxglove panel) can never see the status and show its token prompt —
    the hub just looks unreachable. Regression for the middleware-order
    bug the Phase 3 matrix caught."""
    monkeypatch.setenv("MD_MODE", "fleet")
    monkeypatch.setenv("MD_HUB_AUTH_PASSWORD", "test123")
    monkeypatch.setenv("MD_CORS_ORIGINS", FOXGLOVE)
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/sessions", headers={"Origin": FOXGLOVE})
        assert r.status_code == 401
        assert r.headers.get("access-control-allow-origin") == FOXGLOVE
        # and the token still unlocks it
        r = c.get(
            "/api/sessions",
            headers={"Origin": FOXGLOVE, "Authorization": "Bearer test123"},
        )
        assert r.status_code == 200
