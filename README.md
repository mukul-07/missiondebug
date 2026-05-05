# MissionDebug v0

Local-first debugger for ROS 2 robots. Record → detect → replay, on one laptop.

See [SPEC.md](./SPEC.md) for the full v0 scope.

## Prerequisites

- ROS 2 Humble or Jazzy (sourced in your shell before running the agent)
- Python 3.11+
- Node 20+
- pnpm 9+
- `tmux` (for `make dev`)

## Quickstart

```bash
# 1. install dependencies
make install

# 2. (optional) source ROS 2
source /opt/ros/humble/setup.bash    # or /opt/ros/jazzy/setup.bash

# 3. start everything (tmux session: agent / backend / web)
make dev

# 4. open the UI
open http://localhost:5173

# 5. trigger a manual save while data is flowing
curl -X POST http://localhost:7000/sessions/save -H "content-type: application/json" -d '{}'
```

## Layout

- `agent/` — Python ROS 2 agent (ring buffer, MCAP writer, anomaly detector, control API on :7000)
- `backend/` — FastAPI session index + MCAP file server on :8000
- `web/` — Vite + React + PixiJS timeline scrubber on :5173

## Tests

```bash
make test
```
