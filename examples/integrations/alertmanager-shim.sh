#!/usr/bin/env bash
#
# Tiny shim that turns an Alertmanager webhook into a MissionDebug session.
#
# Alertmanager's webhook payload is a fixed JSON shape with `alerts[]`,
# `commonLabels`, `groupLabels`, etc. — not the `{label}` body our save
# endpoint expects. This shim extracts `alerts[0].labels.alertname` and
# forwards it as the label.
#
# Run as a tiny HTTP service (port 7001 by default) somewhere reachable
# from your Alertmanager. Then point Alertmanager at this shim instead of
# the agent directly.
#
# Requires:  bash + curl + jq + a tool that listens on a port. Two options:
#   1. systemd socket activation (production)
#   2. socat or `nc` for a quick test:
#        socat TCP-LISTEN:7001,fork,reuseaddr EXEC:./alertmanager-shim.sh
#
# Or — much simpler — embed the curl directly in your Alertmanager rule
# via a custom alert template. See docs/INTEGRATIONS.md for that pattern.

set -euo pipefail

AGENT_URL="${MD_AGENT_URL:-http://localhost:7000}"

# Read the Alertmanager body (one POST per invocation; stdin is the body)
body="$(cat)"

# Extract the alert name (defaults to "alertmanager-webhook" if missing).
label="$(echo "$body" | jq -r '.alerts[0].labels.alertname // "alertmanager-webhook"')"

# Forward to MissionDebug. Suppress curl's progress meter; keep the
# response so we can return it (useful for debugging in Alertmanager logs).
curl -sS -X POST "${AGENT_URL}/sessions/save" \
  -H 'Content-Type: application/json' \
  -d "{\"label\":\"alertmanager:${label}\"}"
