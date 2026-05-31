#!/usr/bin/env bash
#
# seed-incidents.sh — populate a local hub with a realistic incident corpus
# so the v2 P3.5 surfaces (summary, "Has this happened before?", fleet
# incident dashboard, resolution editing) have real data to render.
#
# Why curl instead of the agent path: the agent->hub ingest currently drops
# `summary` (known open bug, see CLAUDE.md). Posting straight to the ingest
# endpoint with an explicit summary exercises every downstream surface
# (similarity / dashboard / resolutions) without needing ROS publishers.
#
# Summaries are written in the exact agent summarizer template shape so
# TF-IDF similarity clusters them the way production would (by rule name +
# topic paths). Three battery_low incidents cluster together; one is then
# marked a duplicate so the recurrence KPI is non-zero.
#
# Usage:
#   make dev                       # start the hub first (backend on :8000)
#   ./scripts/seed-incidents.sh    # then seed
#
# Env:
#   HUB        base URL (default http://localhost:8000)
#   MD_TOKEN   bearer token, only if the hub has auth enabled
#
set -euo pipefail

HUB="${HUB:-http://localhost:8000}"
AUTH=()
if [[ -n "${MD_TOKEN:-}" ]]; then
  AUTH=(-H "Authorization: Bearer ${MD_TOKEN}")
fi
now_s="$(date +%s)"

ingest() {
  # ingest <id> <robot> <subsystem> <days_ago> <rule> <duration_s> <size_bytes> <topics_human>
  local payload code
  payload="$(HUB="$HUB" NOW_S="$now_s" python3 - "$@" <<'PY'
import json, os, re, sys
from datetime import datetime, timezone
sid, robot, subsystem, days_ago, rule, dur_s, size, topics_h = sys.argv[1:9]
days_ago, dur_s, size = int(days_ago), int(dur_s), int(size)
now_s = int(os.environ["NOW_S"])
started_s = now_s - days_ago * 86400
started_ms = started_s * 1000
dur_ms = dur_s * 1000
started_str = datetime.fromtimestamp(started_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
topics = [t.split(" (")[0] for t in topics_h.split(", ")]
n = len(topics)
size_kb = f"{size/1024:.1f} KB"
summary = (
    f"Auto-triggered by rule '{rule}' at {started_str} on {robot} "
    f"(subsystem: {subsystem}). Captured {dur_s}.0s across {n} topics: "
    f"{topics_h}. Total payload: {size_kb}."
)
print(json.dumps({
    "session_id": sid, "robot_id": robot,
    "started_at": started_ms, "ended_at": started_ms + dur_ms,
    "duration_ms": dur_ms, "label": f"anomaly:{rule}",
    "topics": topics, "mcap_size_bytes": size,
    "mcap_url": f"http://localhost:7000/mcap?session={sid}",
    "subsystem": subsystem, "summary": summary,
}))
PY
)"
  code="$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" \
      -X POST "${HUB}/api/v1/sessions/ingest" \
      -H 'content-type: application/json' --data-binary "${payload}")"
  printf '  ingest %-8s %-18s %-14s %s ago -> HTTP %s\n' "$1" "$2" "$5" "${4}d" "$code"
}

resolve() {
  # resolve <id> <status> <root_cause> <ticket> <duplicate_of>
  local id="$1" body code
  body="$(python3 - "$2" "$3" "$4" "$5" <<'PY'
import json, sys
status, rc, ticket, dup = sys.argv[1:5]
d = {"status": status, "edited_by": "seed-script"}
if rc:     d["root_cause"] = rc
if ticket: d["linked_ticket"] = ticket
if dup:    d["duplicate_of"] = dup
print(json.dumps(d))
PY
)"
  code="$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" \
      -X PUT "${HUB}/api/v2/sessions/${id}/resolution" \
      -H 'content-type: application/json' --data-binary "${body}")"
  printf '  resolve %-8s %-13s -> HTTP %s\n' "$id" "$2" "$code"
}

heartbeat() {
  # heartbeat <robot> — refresh last_heartbeat so the Agents page shows the
  # robot as healthy rather than "silent / never heartbeated". Ingest alone
  # registers the agent row but leaves last_heartbeat null.
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" \
      -X POST "${HUB}/api/v1/agents/heartbeat" \
      -H 'content-type: application/json' \
      --data-binary "{\"robot_id\":\"$1\",\"agent_version\":\"2.0.0\",\"buffer_size\":600}")"
  printf '  heartbeat %-18s -> HTTP %s\n' "$1" "$code"
}

echo "Seeding incident corpus into ${HUB} ..."
echo "Ingesting sessions:"

# Cluster A — battery_low (power). 201/202 are the canonical resolved
# incidents; 203/204/205 recur against them (the recurrence story).
ingest SES-201 warehouse-bot-03 power 18 battery_low 60 243712 "/battery_state (320 msgs), /cmd_vel (180 msgs), /diagnostics (45 msgs), /odom (600 msgs)"
ingest SES-202 warehouse-bot-07 power 15 battery_low 60 251904 "/battery_state (318 msgs), /cmd_vel (176 msgs), /diagnostics (44 msgs), /odom (590 msgs)"
ingest SES-203 warehouse-bot-03 power 10 battery_low 60 240128 "/battery_state (322 msgs), /cmd_vel (181 msgs), /diagnostics (46 msgs), /odom (604 msgs)"
ingest SES-204 warehouse-bot-05 power  6 battery_low 60 245760 "/battery_state (319 msgs), /cmd_vel (178 msgs), /diagnostics (45 msgs), /odom (598 msgs)"
ingest SES-205 warehouse-bot-07 power  2 battery_low 60 250112 "/battery_state (321 msgs), /cmd_vel (179 msgs), /diagnostics (45 msgs), /odom (601 msgs)"

# Cluster B — topic_dropout (perception). 210 resolved; 211 recurs; 212 active.
ingest SES-210 warehouse-bot-05 perception 16 topic_dropout 60 512000 "/scan (290 msgs), /odom (600 msgs), /tf (1200 msgs), /camera/image_raw (60 msgs)"
ingest SES-211 warehouse-bot-07 perception  8 topic_dropout 60 498688 "/scan (286 msgs), /odom (598 msgs), /tf (1190 msgs), /camera/image_raw (59 msgs)"
ingest SES-212 warehouse-bot-02 perception  3 topic_dropout 60 505856 "/scan (288 msgs), /odom (599 msgs), /tf (1195 msgs), /camera/image_raw (60 msgs)"

# Cluster C — stall (navigation). 220 resolved; 221 recurs; 222 active.
ingest SES-220 warehouse-bot-01 navigation 14 stall 60 198656 "/cmd_vel (200 msgs), /odom (600 msgs), /scan (290 msgs), /move_base/status (30 msgs)"
ingest SES-221 warehouse-bot-05 navigation  5 stall 60 201728 "/cmd_vel (198 msgs), /odom (602 msgs), /scan (288 msgs), /move_base/status (31 msgs)"
ingest SES-222 warehouse-bot-01 navigation  1 stall 60 199680 "/cmd_vel (201 msgs), /odom (601 msgs), /scan (289 msgs), /move_base/status (30 msgs)"

# Cluster D — path_deviation (navigation).
ingest SES-230 warehouse-bot-02 navigation  9 path_deviation 60 187392 "/cmd_vel (205 msgs), /odom (601 msgs), /plan (40 msgs), /tf (1205 msgs)"

# A still-open manual capture with no cluster.
ingest SES-240 warehouse-bot-01 navigation  1 manual 45 176128 "/cmd_vel (150 msgs), /odom (450 msgs), /scan (220 msgs)"

echo "Setting resolutions:"
resolve SES-201 resolved "Battery pack cell 3 degraded; replaced module and recalibrated SoC curve" "JIRA-4471" ""
resolve SES-202 resolved "Low-charge cutoff misconfigured at 15%; raised fleet default to 25%" "JIRA-4480" ""
resolve SES-203 duplicate "" "" "SES-201"
resolve SES-204 duplicate "" "" "SES-201"
resolve SES-205 duplicate "" "" "SES-202"
resolve SES-210 resolved "LIDAR USB hub browned out under load; moved the scan driver to a powered hub" "JIRA-4502" ""
resolve SES-211 duplicate "" "" "SES-210"
resolve SES-212 investigating "" "" ""
resolve SES-220 resolved "Costmap inflation radius too large near racking; tuned per-aisle" "LINEAR-882" ""
resolve SES-221 duplicate "" "" "SES-220"
resolve SES-222 investigating "" "" ""
resolve SES-230 wont_fix "Known GPS multipath in aisle 7; operational workaround documented" "" ""
# SES-240 intentionally left open.

echo "Sending heartbeats (so the fleet shows healthy, not silent):"
for r in warehouse-bot-01 warehouse-bot-02 warehouse-bot-03 \
         warehouse-bot-05 warehouse-bot-07; do
  heartbeat "$r"   # expect HTTP 204
done

echo
echo "Done. Now open:"
echo "  ${HUB}/                  session list (summaries on each card)"
echo "  ${HUB}/fleet/incidents   the dashboard (MTTR / recurrence / top patterns)"
echo "  open SES-203 -> 'Has this happened before?' should surface SES-201 (resolved) + SES-202"
