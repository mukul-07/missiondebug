#!/usr/bin/env bash
# Launch agent + backend + web in a tmux session.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION="missiondebug"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required for scripts/dev.sh" >&2
  exit 1
fi

# Auto-detect a ROS 2 install (humble preferred, then jazzy).
ROS_SETUP=""
for candidate in /opt/ros/humble/setup.bash /opt/ros/jazzy/setup.bash; do
  if [ -f "$candidate" ]; then
    ROS_SETUP="$candidate"
    break
  fi
done

# The agent needs ROS sourced + its python venv on PATH.
AGENT_CMD=""
if [ -n "$ROS_SETUP" ]; then
  AGENT_CMD+="source '$ROS_SETUP'; "
fi
if [ -f "$ROOT/agent/.venv/bin/activate" ]; then
  AGENT_CMD+="source '$ROOT/agent/.venv/bin/activate'; "
fi
AGENT_CMD+="python -m missiondebug_agent.main --config config.example.yaml"

# Backend just needs its venv (or uv run as fallback).
BACKEND_CMD=""
if [ -f "$ROOT/backend/.venv/bin/activate" ]; then
  BACKEND_CMD+="source '$ROOT/backend/.venv/bin/activate'; "
fi
if [ "${MD_FIXTURES:-}" = "1" ]; then
  BACKEND_CMD+="MD_FIXTURES=1 "
fi
BACKEND_CMD+="python -m missiondebug_backend.main --sessions-dir ../agent/sessions"

WEB_CMD="pnpm dev"

tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -n agent -c "$ROOT/agent" \
  "bash -lc \"$AGENT_CMD\""

tmux split-window -t "$SESSION":agent -h -c "$ROOT/backend" \
  "bash -lc \"$BACKEND_CMD\""

tmux split-window -t "$SESSION":agent -v -c "$ROOT/web" \
  "bash -lc \"$WEB_CMD\""

tmux select-layout -t "$SESSION":agent tiled
tmux attach -t "$SESSION"
