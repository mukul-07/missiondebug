"""v2 EE — the license-key engine (the "lock").

Generates an ephemeral Ed25519 keypair per test and exercises the full
sign → verify round-trip, so it never depends on a bundled key.
"""

from __future__ import annotations

import json

from missiondebug_backend.ee.licensing import (
    License,
    _b64url_encode,
    generate_keypair,
    load_license,
    sign_license,
    verify_license_key,
)


def _issue(priv: str, **over) -> str:
    payload = {
        "customer": "Acme Robotics",
        "robots": 100,
        "features": ["alerting", "lifecycle"],
        "expires_at": None,
        "id": "MD-test123",
    }
    payload.update(over)
    return sign_license(payload, private_key_b64=priv)


def test_roundtrip_valid_key():
    priv, pub = generate_keypair()
    lic = verify_license_key(_issue(priv), public_key_b64=pub)
    assert lic.valid
    assert lic.customer == "Acme Robotics"
    assert lic.robots == 100
    assert lic.allows("alerting") and lic.allows("lifecycle")
    assert not lic.allows("sso")            # feature not in the key
    assert lic.license_id == "MD-test123"   # the watermark


def test_expired_key_is_unlicensed():
    priv, pub = generate_keypair()
    key = _issue(priv, expires_at=1000)     # epoch 1000 = 1970
    lic = verify_license_key(key, public_key_b64=pub, now=2000)
    assert not lic.valid
    assert not lic.allows("alerting")


def test_perpetual_key_never_expires():
    priv, pub = generate_keypair()
    key = _issue(priv, expires_at=None)
    lic = verify_license_key(key, public_key_b64=pub, now=10**12)
    assert lic.valid


def test_cannot_change_robot_count_and_reuse_signature():
    """The core anti-forgery property: swap a bigger robot count into the
    payload but keep the original signature → rejected."""
    priv, pub = generate_keypair()
    _, sig_b64 = _issue(priv, robots=100).split(".", 1)
    forged_payload = _b64url_encode(
        json.dumps(
            {"customer": "Acme Robotics", "robots": 99999,
             "features": ["alerting", "lifecycle"], "expires_at": None,
             "id": "MD-test123"},
            separators=(",", ":"), sort_keys=True,
        ).encode()
    )
    lic = verify_license_key(forged_payload + "." + sig_b64, public_key_b64=pub)
    assert not lic.valid


def test_wrong_public_key_rejected():
    priv, _ = generate_keypair()
    _, other_pub = generate_keypair()
    lic = verify_license_key(_issue(priv), public_key_b64=other_pub)
    assert not lic.valid


def test_missing_or_garbled_keys():
    _, pub = generate_keypair()
    assert not verify_license_key("", public_key_b64=pub).valid
    assert not verify_license_key("not-a-key", public_key_b64=pub).valid
    assert not verify_license_key("a.b", public_key_b64=pub).valid


def test_no_public_key_means_unlicensed():
    priv, _ = generate_keypair()
    assert not verify_license_key(_issue(priv), public_key_b64="").valid


def test_unlicensed_allows_nothing():
    lic = License.unlicensed()
    assert not lic.valid
    assert not lic.allows("alerting")
    assert lic.status()["licensed"] is False


def test_load_license_reads_env(monkeypatch):
    priv, pub = generate_keypair()
    monkeypatch.setenv("MD_LICENSE_KEY", _issue(priv))
    lic = load_license(public_key_b64=pub)
    assert lic.valid and lic.customer == "Acme Robotics"


def test_load_license_unlicensed_when_no_env(monkeypatch):
    _, pub = generate_keypair()
    monkeypatch.delenv("MD_LICENSE_KEY", raising=False)
    assert not load_license(public_key_b64=pub).valid


# ---- the key-maker CLI -------------------------------------------------


def test_cli_issue_prints_valid_key(capsys):
    from missiondebug_backend.ee.make_license import main

    priv, pub = generate_keypair()
    main([
        "issue", "--customer", "Acme", "--robots", "10", "--days", "30",
        "--features", "alerting,lifecycle", "--private-key", priv,
    ])
    key = capsys.readouterr().out.strip().splitlines()[-1]  # last line is the key
    lic = verify_license_key(key, public_key_b64=pub)
    assert lic.valid and lic.customer == "Acme" and lic.robots == 10
    assert lic.allows("alerting") and lic.allows("lifecycle")
    assert lic.expires_at is not None       # 30-day expiry set


def test_cli_genkey_prints_keypair(capsys):
    from missiondebug_backend.ee.make_license import main

    main(["genkey"])
    out = capsys.readouterr().out
    assert "PRIVATE KEY" in out and "PUBLIC KEY" in out


def test_real_key_via_env_unlocks_the_gate(tmp_path, monkeypatch):
    """End-to-end: a signed key + public key in the environment flow through
    load_license() and unlock a gated (ee/) feature in the live app."""
    from fastapi.testclient import TestClient

    from missiondebug_backend.main import build_app

    priv, pub = generate_keypair()
    key = _issue(priv, features=["lifecycle"])
    monkeypatch.setenv("MD_LICENSE_PUBKEY", pub)
    monkeypatch.setenv("MD_LICENSE_KEY", key)

    # license=None → build_app calls load_license() and reads the env above.
    app = build_app(tmp_path / "s", tmp_path / "db.sqlite3", cold_after_days=30)
    with TestClient(app) as client:
        assert client.get("/api/admin/license").json()["customer"] == "Acme Robotics"
        assert client.post("/api/admin/lifecycle/sweep").status_code == 200  # unlocked
