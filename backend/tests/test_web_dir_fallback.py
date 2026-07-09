"""--web-dir resolution: explicit value wins; empty auto-detects the .deb
web install (only when it holds a real build); otherwise headless.

Regression for the field failure where an emptied /etc/missiondebug/
backend.env (missing MD_WEB_DIR) turned the dashboard into a bare JSON 404
with no error anywhere.
"""

from pathlib import Path

from missiondebug_backend import main as main_mod


def test_explicit_value_wins(tmp_path, monkeypatch):
    deb_dir = tmp_path / "deb-web"
    deb_dir.mkdir()
    (deb_dir / "index.html").write_text("<html></html>")
    monkeypatch.setattr(main_mod, "DEB_WEB_DIR", deb_dir)

    assert main_mod._resolve_web_dir("/some/other/dir") == Path("/some/other/dir")


def test_empty_falls_back_to_deb_install(tmp_path, monkeypatch):
    deb_dir = tmp_path / "deb-web"
    deb_dir.mkdir()
    (deb_dir / "index.html").write_text("<html></html>")
    monkeypatch.setattr(main_mod, "DEB_WEB_DIR", deb_dir)

    assert main_mod._resolve_web_dir("") == deb_dir


def test_empty_without_deb_install_is_headless(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "DEB_WEB_DIR", tmp_path / "absent")

    assert main_mod._resolve_web_dir("") is None


def test_leftover_empty_dir_does_not_count_as_a_build(tmp_path, monkeypatch):
    deb_dir = tmp_path / "deb-web"
    deb_dir.mkdir()  # exists but no index.html
    monkeypatch.setattr(main_mod, "DEB_WEB_DIR", deb_dir)

    assert main_mod._resolve_web_dir("") is None
