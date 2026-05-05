#!/usr/bin/env bash
# Seed a fixture session by replaying a small public ROS 2 bag through the
# running agent. Requires ROS 2 + a configured 'fixture' bag in fixtures/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE_DIR="$ROOT/fixtures"
mkdir -p "$FIXTURE_DIR"

if [ ! -d "$FIXTURE_DIR/sample_bag" ]; then
  echo "No fixture bag at $FIXTURE_DIR/sample_bag." >&2
  echo "Place a ROS 2 bag (rosbag2 sqlite) at that path. v0 ships without one." >&2
  exit 1
fi

# Kick the agent so it's listening, then play the bag, then save.
curl -sf http://127.0.0.1:7000/healthz >/dev/null \
  || { echo "Agent isn't running on :7000. Start it first."; exit 1; }

ros2 bag play "$FIXTURE_DIR/sample_bag" &
BAG_PID=$!

sleep 8
curl -X POST http://127.0.0.1:7000/sessions/save \
  -H "content-type: application/json" \
  -d '{"label": "fixture"}' | tee /dev/stderr

wait $BAG_PID || true
