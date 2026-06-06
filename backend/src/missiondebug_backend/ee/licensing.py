# MissionDebug Enterprise Edition — Commercial License, NOT MIT.
# Part of the paid Fleet/Enterprise tiers. Source is visible for evaluation and
# audit; commercial/production use requires a paid license + key. See ee/LICENSE
# and LICENSING.md. Copyright (c) 2026 MissionDebug. All rights reserved.
"""License-key engine (the "lock").

A license key is a signed token you issue to a paying customer. The hub
verifies it and reads what it unlocks (features, robot limit, expiry,
customer identity). Without a valid key the paid features stay off.

Why it can't be forged even though this code is visible: the key is signed
with your **private** Ed25519 key (kept secret, only on your machine / the
key-maker tool). The hub only carries the matching **public** key, which can
*verify* a signature but cannot *create* one. So a customer can read exactly
how the check works and still cannot mint their own keys.

Key format (JWT-ish, no header): ``base64url(payload_json).base64url(signature)``
The signature is over the raw payload bytes; verification checks the signature
over those same bytes, then parses the JSON — so the signed and parsed bytes
are always identical.

The ``customer`` + ``id`` fields baked into the signed payload are the
**watermark** — a leaked/misused key traces straight back to who it was issued
to.

Crypto lives in the optional ``[license]`` extra (``cryptography``). If it
isn't installed, verification degrades to "unlicensed" (paid features off) and
logs a hint — the free MIT core stays dependency-light (Hard Rule 25).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Your license-signing PUBLIC key (base64url of the 32 raw Ed25519 bytes).
# Generate your keypair with `python -m missiondebug_backend.ee.make_license
# genkey`, keep the PRIVATE key secret, and paste the PUBLIC key here (or set
# MD_LICENSE_PUBKEY at runtime). Empty default = no bundled key → every key is
# rejected until you set your own.
_BUNDLED_PUBLIC_KEY_B64 = ""


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@dataclass(frozen=True)
class License:
    customer: str | None
    robots: int
    features: frozenset[str]
    expires_at: int | None  # epoch seconds; None = perpetual
    license_id: str | None  # the watermark
    valid: bool

    @classmethod
    def unlicensed(cls) -> License:
        """The no-op license — allows nothing. Callers always hold a License
        (never None), so gate checks stay branch-free."""
        return cls(None, 0, frozenset(), None, None, False)

    def allows(self, feature: str) -> bool:
        return self.valid and feature in self.features

    def status(self) -> dict:
        return {
            "licensed": self.valid,
            "customer": self.customer,
            "robots": self.robots,
            "features": sorted(self.features),
            "expires_at": self.expires_at,
            "license_id": self.license_id,
        }


def _public_key_b64(override: str | None) -> str:
    if override is not None:
        return override
    return os.environ.get("MD_LICENSE_PUBKEY", "").strip() or _BUNDLED_PUBLIC_KEY_B64


def verify_license_key(
    key: str, *, public_key_b64: str | None = None, now: int | None = None
) -> License:
    """Verify a signed key. Returns ``License.unlicensed()`` on ANY problem
    (missing key, no public key, bad signature, malformed, expired). Never
    raises — a broken key must never take the hub down, just leave it unpaid."""
    pub_b64 = _public_key_b64(public_key_b64)
    if not key or not pub_b64:
        return License.unlicensed()
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:
        log.warning(
            "license: the 'cryptography' library is not installed, so paid "
            "features stay off. Install it with: pip install "
            "'missiondebug-backend[license]'."
        )
        return License.unlicensed()
    try:
        payload_b64, sig_b64 = key.strip().split(".", 1)
        payload_bytes = _b64url_decode(payload_b64)
        signature = _b64url_decode(sig_b64)
        pub = Ed25519PublicKey.from_public_bytes(_b64url_decode(pub_b64))
        try:
            pub.verify(signature, payload_bytes)
        except InvalidSignature:
            log.warning("license: signature did not verify — key not genuine")
            return License.unlicensed()
        data = json.loads(payload_bytes)
        expires_at = data.get("expires_at")
        t = now if now is not None else int(time.time())
        if expires_at is not None and t > int(expires_at):
            log.warning("license: expired on %s", expires_at)
            return License.unlicensed()
        return License(
            customer=data.get("customer"),
            robots=int(data.get("robots", 0)),
            features=frozenset(data.get("features", [])),
            expires_at=int(expires_at) if expires_at is not None else None,
            license_id=data.get("id"),
            valid=True,
        )
    except Exception:
        log.warning("license: could not parse key", exc_info=True)
        return License.unlicensed()


def load_license(*, public_key_b64: str | None = None) -> License:
    """Read + verify ``MD_LICENSE_KEY`` from the environment."""
    return verify_license_key(
        os.environ.get("MD_LICENSE_KEY", ""), public_key_b64=public_key_b64
    )


# ---- issuing side (used by the key-maker tool; needs the PRIVATE key) ----


def generate_keypair() -> tuple[str, str]:
    """Make a fresh Ed25519 keypair. Returns (private_b64, public_b64).
    Keep the private one SECRET; bundle the public one in the hub."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    priv_b = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_b = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return _b64url_encode(priv_b), _b64url_encode(pub_b)


def sign_license(payload: dict, *, private_key_b64: str) -> str:
    """Sign a license payload with your private key → the key string you give
    the customer."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.from_private_bytes(_b64url_decode(private_key_b64))
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = priv.sign(payload_bytes)
    return _b64url_encode(payload_bytes) + "." + _b64url_encode(signature)
