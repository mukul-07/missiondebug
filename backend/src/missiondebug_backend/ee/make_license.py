# MissionDebug Enterprise Edition — Commercial License, NOT MIT.
# Part of the paid Fleet/Enterprise tiers. Source is visible for evaluation and
# audit; commercial/production use requires a paid license + key. See ee/LICENSE
# and LICENSING.md. Copyright (c) 2026 MissionDebug. All rights reserved.
"""Key-maker CLI — the vendor tool for issuing license keys.

Run by YOU only; it needs your secret private key, which the hub never has.

  # one-time: make your signing keypair
  python -m missiondebug_backend.ee.make_license genkey

  # after a customer pays: mint their key
  MD_LICENSE_PRIVKEY=<your-private-key> \\
    python -m missiondebug_backend.ee.make_license issue \\
      --customer "Acme Robotics" --robots 100 --days 365 \\
      --features alerting,lifecycle

Then email the printed key to the customer; they set MD_LICENSE_KEY=<key>.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

from .licensing import (
    _b64url_decode,
    _b64url_encode,
    generate_keypair,
    sign_license,
    verify_license_key,
)

_DEFAULT_FEATURES = "alerting,lifecycle"


def _genkey(_args: argparse.Namespace) -> None:
    priv, pub = generate_keypair()
    print("Generated an Ed25519 license-signing keypair.\n")
    print("PRIVATE KEY  — keep secret, NEVER commit. Use it to issue keys:")
    print(f"  export MD_LICENSE_PRIVKEY={priv}\n")
    print("PUBLIC KEY   — paste into ee/licensing.py (_BUNDLED_PUBLIC_KEY_B64)")
    print("               or ship as MD_LICENSE_PUBKEY. Safe to be public:")
    print(f"  {pub}")


def _issue(args: argparse.Namespace) -> None:
    priv = args.private_key or os.environ.get("MD_LICENSE_PRIVKEY", "").strip()
    if not priv:
        sys.exit("error: pass --private-key or set MD_LICENSE_PRIVKEY (from `genkey`)")

    expires_at = int(time.time()) + args.days * 86_400 if args.days > 0 else None
    features = sorted({f.strip() for f in args.features.split(",") if f.strip()})
    payload = {
        "customer": args.customer,
        "robots": args.robots,
        "features": features,
        "expires_at": expires_at,
        "id": args.id or f"MD-{uuid.uuid4().hex[:12]}",  # the watermark
    }
    key = sign_license(payload, private_key_b64=priv)

    # Self-verify so you never email a key that won't validate: derive the
    # public key from the private one and check the round-trip.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pub_b = (
        Ed25519PrivateKey.from_private_bytes(_b64url_decode(priv))
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    check = verify_license_key(key, public_key_b64=_b64url_encode(pub_b))
    if not check.valid:  # pragma: no cover - defensive
        sys.exit("error: issued key failed self-verification; not emitting it")

    print(f"# License for {payload['customer']} "
          f"({payload['robots']} robots, "
          f"{'perpetual' if expires_at is None else f'expires in {args.days}d'}, "
          f"features: {', '.join(features) or 'none'}, id: {payload['id']})")
    print("# Give the customer this — they set MD_LICENSE_KEY=<key>:")
    print(key)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="make_license", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("genkey", help="generate a new signing keypair (run once)")

    p = sub.add_parser("issue", help="issue a license key for a customer")
    p.add_argument("--customer", required=True, help="customer name (also the watermark)")
    p.add_argument("--robots", type=int, default=0, help="licensed robots (0 = unlimited)")
    p.add_argument("--days", type=int, default=365, help="validity in days (0 = perpetual)")
    p.add_argument("--features", default=_DEFAULT_FEATURES, help="comma list of features")
    p.add_argument("--id", default="", help="explicit license id (default: random)")
    p.add_argument("--private-key", default="", help="signing key (else MD_LICENSE_PRIVKEY)")

    args = parser.parse_args(argv)
    if args.cmd == "genkey":
        _genkey(args)
    elif args.cmd == "issue":
        _issue(args)


if __name__ == "__main__":  # pragma: no cover
    main()
