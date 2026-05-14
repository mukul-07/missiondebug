"""Regression test for SPA deep-link fallback (Copy-link bug, 2026-05-13).

Pasting a session detail URL (e.g. /sessions/robot-001_2026...?t=10.0)
into a fresh tab triggers a GET request to the backend for that path.
Default StaticFiles returns 404 JSON, breaking the share-link UX.
SpaStaticFiles should fall back to index.html so React Router handles
the path client-side.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app


def _setup(tmp_path):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    # Minimal SPA index — real Vite output would be much bigger but the
    # marker is all the fallback test needs to recognize this as the shell.
    (web_dir / "index.html").write_text(
        '<!doctype html><html><head><title>MissionDebug</title></head>'
        '<body><div id="root"></div></body></html>'
    )
    # An asset that does exist on disk — verifies normal serving still works.
    (web_dir / "assets").mkdir()
    (web_dir / "assets" / "index-abc.js").write_text("// real asset")

    app = build_app(
        sessions_dir=tmp_path / "sessions",
        db_path=tmp_path / "x.sqlite3",
        web_dir=web_dir,
    )
    return TestClient(app)


def test_root_serves_index_html(tmp_path):
    """Baseline: '/' returns the SPA shell."""
    c = _setup(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "<title>MissionDebug</title>" in r.text


def test_deep_link_returns_index_html(tmp_path):
    """Pasting a session-detail URL into a new tab serves the SPA shell,
    not a JSON 404. The SPA reads location.pathname and routes."""
    c = _setup(tmp_path)
    r = c.get("/sessions/robot-001_20260512T150000Z")
    assert r.status_code == 200
    assert "<title>MissionDebug</title>" in r.text


def test_deep_link_with_query_string(tmp_path):
    """The ?t=10.03 timestamp query is preserved on the SPA shell;
    the query reaches the client unchanged."""
    c = _setup(tmp_path)
    r = c.get("/sessions/robot-001_20260512T150000Z?t=10.03")
    assert r.status_code == 200
    assert "<title>MissionDebug</title>" in r.text


def test_real_asset_still_404s_when_missing(tmp_path):
    """Asset paths (containing a dot in the final segment) must NOT
    silently serve index.html — failing fast on missing assets is the
    only way the dev catches a broken <script src=...> tag."""
    c = _setup(tmp_path)
    r = c.get("/assets/does-not-exist.js")
    assert r.status_code == 404


def test_real_asset_serves_correctly(tmp_path):
    """Sanity: real assets on disk are served as files."""
    c = _setup(tmp_path)
    r = c.get("/assets/index-abc.js")
    assert r.status_code == 200
    assert "// real asset" in r.text


def test_api_route_unaffected(tmp_path):
    """The SPA fallback must NOT intercept /api/* — those routes are
    registered before the static mount and return real responses."""
    c = _setup(tmp_path)
    r = c.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": [], "robots": []}


def test_unknown_api_route_returns_json_404(tmp_path):
    """A bad /api/* path must NOT fall through to the SPA shell —
    that would mask client bugs trying to call missing endpoints
    (curl, agents, integration scripts all expect JSON 404)."""
    c = _setup(tmp_path)
    r = c.get("/api/this-does-not-exist")
    assert r.status_code == 404
    # Body should be a real 404, not the HTML shell.
    assert "<title>" not in r.text
