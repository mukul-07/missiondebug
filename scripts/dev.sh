#!/usr/bin/env bash
# Launch agent + backend + web in a tmux session.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION="missiondebug"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required for scripts/dev.sh" >&2
  exit 1
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -n agent -c "$ROOT/agent" \
  "python -m missiondebug_agent.main --config config.example.yaml"

tmux split-window -t "$SESSION":agent -h -c "$ROOT/backend" \
  "python -m missiondebug_backend.main --sessions-dir ../agent/sessions"

tmux split-window -t "$SESSION":agent -v -c "$ROOT/web" \
  "pnpm dev"

tmux select-layout -t "$SESSION":agent tiled
tmux attach -t "$SESSION"
