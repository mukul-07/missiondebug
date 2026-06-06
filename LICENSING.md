# Licensing

MissionDebug is **open-core**. The split is deliberate and simple:

| Part | License | What it is |
|---|---|---|
| **Everything except `ee/`** | **MIT** (see [`LICENSE`](./LICENSE)) | The free **Community** tier — capture, replay, incident dashboard, similarity, resolutions, OpenTelemetry export, the bring-your-own-key AI agent, the hub core. Free forever, including commercial use. |
| **The `ee/` directories** | **MissionDebug Commercial License** (see [`backend/src/missiondebug_backend/ee/LICENSE`](./backend/src/missiondebug_backend/ee/LICENSE)) | The paid **Fleet / Enterprise** features. Source is visible for evaluation and audit, but **commercial / production use requires a paid license + key**. |

## What this means in practice

- **Community (free, MIT):** clone it, run it, use it commercially — no payment, forever.
- **Fleet / Enterprise (paid, proprietary):** the code under any `ee/` directory is *source-available, not open source*. You may read and evaluate it; running it in production or for commercial purposes needs a paid license. Today that covers **alerting** and **lifecycle policies**; future paid features (SSO/RBAC, the cross-fleet incident network, managed AI) will also live under `ee/`.

## How to tell which is which

- Anything inside an `ee/` folder, or with a file header that says *"MissionDebug Enterprise Edition — Commercial License, NOT MIT,"* is proprietary.
- Everything else is MIT.

## Notes

- The Commercial License text is a **template** — have a lawyer review it before you sell.
- Versions of any feature that were previously published under MIT remain MIT in those released versions; the Commercial License applies going forward to code in `ee/`.

## Issuing license keys (vendor runbook)

The `ee/` features stay off until the hub has a valid license key. Keys are
Ed25519-signed — only the holder of the private key (you) can mint them, even
though the verification code is public.

**1. One-time — make your signing keypair:**
```bash
python -m missiondebug_backend.ee.make_license genkey
```
Keep the **private** key secret (password manager / CI secret) — never commit
it. Put the **public** key in `ee/licensing.py` (`_BUNDLED_PUBLIC_KEY_B64`) or
ship it as `MD_LICENSE_PUBKEY`; it's safe to be public.

**2. When a customer pays — mint their key:**
```bash
MD_LICENSE_PRIVKEY=<private> python -m missiondebug_backend.ee.make_license issue \
  --customer "Acme Robotics" --robots 100 --days 365 --features alerting,lifecycle
```
Email them the printed key.

**3. The customer installs it:**
- Install the extra on the hub: `pip install 'missiondebug-backend[license]'`.
- Set `MD_LICENSE_KEY=<key>` (e.g. in `.env` / compose), restart.
- Verify: `GET /api/admin/license` → `{"licensed": true, ...}`, or the badge on
  the Agents page.

**Over-deployment** is reported automatically: `/api/admin/license` shows
`robots_active` vs the licensed `robots` cap (`over_limit: true` if exceeded),
computed locally on the hub — no phone-home, so air-gapped fleets are fine.

---

To obtain a paid license, contact: **sales@missiondebug.example** (update this).
