# MissionDebug v0 — Build Specification

## Project Overview

MissionDebug v0 is a local-first debugger for ROS 2 robots. A small agent
runs alongside a customer's ROS 2 stack, continuously buffering data in
memory. When an anomaly is detected (or the user manually flags), it
persists a "session" — a time-bounded slice of all topics, video, and
metadata. The user opens a web UI, picks a session, and scrubs through a
synchronized timeline of everything the robot saw and did.

v0 has ONE goal: prove the record → detect → replay loop works for a
single ROS 2 robot on a single developer's laptop. No cloud, no auth, no
multi-robot, no fleet view. If v0 works, v1 adds the rest.

## What v0 IS

- A Python ROS 2 agent that subscribes to a configurable list of topics
- A 60-second rolling ring buffer in RAM
- Manual session save (HTTP POST to the agent) and basic anomaly trigger
  (velocity == 0 for >5s while in autonomous mode)
- MCAP file output to local disk
- A FastAPI backend that lists sessions and streams MCAP data
- A React + TypeScript frontend with a working timeline scrubber that
  displays: 2 video tracks, 1 transform/pose track, and 1 numeric
  telemetry chart, time-synced

## What v0 IS NOT

- Not multi-robot. One robot. Period.
- Not cloud-hosted. Localhost only.
- Not authenticated. No login, no users, no orgs.
- Not real-time streaming. Replay only.
- Not Foxglove-replacement-grade visualization. 4 panels, that's it.
- Not Rust. Python agent for v0. Rust comes in v1.
- Not production-deployable. Dev tool only.

If a feature is not explicitly listed in "v0 IS", it does not exist in v0.
Reject scope creep aggressively.

## Tech Stack

### Agent (Python 3.11+)
- `rclpy` for ROS 2 (Humble or Jazzy)
- `mcap` + `mcap-ros2-support` for MCAP writing
- `fastapi` + `uvicorn` for the agent's local HTTP control API
- `pydantic` for config schema
- Standard library `collections.deque` for ring buffer
- `pyyaml` for config files

### Backend (Python 3.11+)
- `fastapi` + `uvicorn`
- `sqlite3` (stdlib) for session metadata
- `mcap` for reading MCAP files server-side (metadata extraction only)
- Files served as static assets via FastAPI

### Frontend (TypeScript)
- Vite + React 18 + TypeScript (strict mode)
- Tailwind CSS + shadcn/ui (only Button, Card, Slider, Dialog)
- Zustand for client state
- TanStack Query for server state
- `@mcap/core` and `@mcap/support` for browser-side MCAP parsing
- PixiJS v8 for the timeline canvas renderer
- Native HTML `<video>` for video playback (HLS later, not v0)

### Tooling
- pnpm workspaces (monorepo)
- Python managed via `uv` (faster than pip, lockfile-based)
- ruff for Python lint/format
- biome for TS lint/format (faster than eslint+prettier)
- pre-commit hooks

## Repository Layout

```
missiondebug/
├── SPEC.md                    # this file
├── README.md
├── pnpm-workspace.yaml
├── package.json
├── .pre-commit-config.yaml
├── agent/                     # Python ROS 2 agent
│   ├── pyproject.toml
│   ├── src/missiondebug_agent/
│   │   ├── __init__.py
│   │   ├── main.py            # entry point
│   │   ├── config.py          # pydantic config schema
│   │   ├── ring_buffer.py     # in-memory rolling buffer
│   │   ├── ros_bridge.py      # rclpy subscribers
│   │   ├── mcap_writer.py     # session persistence
│   │   ├── anomaly.py         # simple anomaly detectors
│   │   └── http_api.py        # local control API
│   ├── tests/
│   └── config.example.yaml
├── backend/                   # FastAPI session server
│   ├── pyproject.toml
│   ├── src/missiondebug_backend/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── db.py              # sqlite schema + queries
│   │   ├── routes/
│   │   │   ├── sessions.py
│   │   │   └── files.py
│   │   └── mcap_meta.py       # MCAP metadata extraction
│   └── tests/
├── web/                       # React frontend
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/               # fetch wrappers
│   │   ├── components/
│   │   │   ├── ui/            # shadcn components
│   │   │   ├── SessionList.tsx
│   │   │   └── timeline/
│   │   │       ├── Timeline.tsx          # PixiJS root
│   │   │       ├── TrackVideo.tsx
│   │   │       ├── TrackPose.tsx
│   │   │       ├── TrackChart.tsx
│   │   │       └── Playhead.ts
│   │   ├── hooks/
│   │   ├── stores/
│   │   │   └── playback.ts    # zustand
│   │   └── workers/
│   │       └── mcap-decoder.ts
│   └── public/
└── scripts/
    ├── dev.sh                 # tmux-based dev runner
    └── seed-fixture.sh        # produce a fixture session
```

## Build Plan — Phased

### Phase 0 — Repo scaffold (target: 1 hour)

1. Initialize monorepo with pnpm workspaces.
2. Create `agent/`, `backend/`, `web/` package skeletons.
3. Set up ruff, biome, pre-commit.
4. README with one-line description and dev setup.
5. .gitignore for Python, Node, MCAP files, sqlite db.

Deliverable: `pnpm install && uv sync` works in all three packages.

### Phase 1 — Agent: ring buffer + ROS subscriptions (target: 1 day)

1. `config.py`: pydantic schema for the agent config:
   ```yaml
   robot_id: "robot-001"
   buffer_seconds: 60
   topics:
     - { name: "/tf", type: "tf2_msgs/msg/TFMessage" }
     - { name: "/cmd_vel", type: "geometry_msgs/msg/Twist" }
     - { name: "/camera/image_raw/compressed", type: "sensor_msgs/msg/CompressedImage" }
   output_dir: "./sessions"
   anomaly:
     stall_velocity_threshold: 0.01
     stall_duration_seconds: 5.0
   ```
2. `ring_buffer.py`: thread-safe deque-of-(timestamp, topic, message_bytes)
   with O(1) append and O(n) snapshot. Bytes only — no message
   deserialization in the buffer.
3. `ros_bridge.py`: subscribes to configured topics; serializes each
   incoming message to bytes via rclpy's serialize_message; pushes into
   ring buffer with monotonic timestamp.
4. Unit tests for ring buffer (eviction, snapshot correctness, thread
   safety with concurrent producers).

Deliverable: Run agent against `ros2 bag play` of a sample bag; verify
ring buffer fills and evicts correctly via debug log.

### Phase 2 — Agent: MCAP writer + manual session save (target: 1 day)

1. `mcap_writer.py`: takes a ring buffer snapshot + metadata, writes a
   valid MCAP file with proper schemas, channels, and message records.
   Use `mcap-ros2-support` for ROS 2 schema registration.
2. `http_api.py`: FastAPI app with one endpoint:
   `POST /sessions/save` — flushes current ring buffer to a new MCAP
   file in `output_dir` with filename `{robot_id}_{iso8601}.mcap`.
   Returns `{ "session_id": "...", "path": "...", "duration_s": ..., "topics": [...] }`.
3. Validate output MCAP files open cleanly in `mcap` CLI tool
   (`mcap doctor session.mcap` should pass).

Deliverable: `curl -X POST localhost:7000/sessions/save` produces a valid
MCAP that opens in Foxglove Studio.

### Phase 3 — Agent: anomaly detector (target: 0.5 day)

1. `anomaly.py`: implements ONE detector for v0 — stall detector.
   Subscribes to `/cmd_vel`. If linear.x < threshold AND angular.z <
   threshold for >= stall_duration_seconds, emit a "stall" anomaly event.
2. On anomaly, automatically trigger session save (call same code path
   as manual save) with a label `anomaly:stall`.
3. Cooldown: don't fire again for 30s after a save.

Deliverable: Replay a bag with a deliberate 7-second stall; confirm a
session is saved automatically with the right label.

### Phase 4 — Backend: session index + MCAP metadata (target: 1 day)

1. `db.py`: sqlite schema:
   ```sql
   CREATE TABLE sessions (
     id TEXT PRIMARY KEY,
     robot_id TEXT NOT NULL,
     started_at INTEGER NOT NULL,  -- unix ms
     ended_at INTEGER NOT NULL,
     duration_ms INTEGER NOT NULL,
     label TEXT,
     mcap_path TEXT NOT NULL,
     mcap_size_bytes INTEGER NOT NULL,
     topics_json TEXT NOT NULL,
     created_at INTEGER NOT NULL
   );
   CREATE INDEX idx_sessions_started_at ON sessions(started_at DESC);
   ```
2. On startup, scan `agent/sessions/` directory; for each .mcap file not
   in the db, extract metadata (start time, end time, topic list, sizes)
   via `mcap_meta.py`, insert row.
3. Routes:
   - `GET /api/sessions` — list, paginated, newest first
   - `GET /api/sessions/{id}` — full detail with topic list
   - `GET /api/sessions/{id}/mcap` — serves the MCAP file with HTTP
     range support (critical for browser streaming)
4. CORS open to localhost:5173 (the Vite dev server).

Deliverable: `curl localhost:8000/api/sessions` returns a list including
the session saved in phase 3.

### Phase 5 — Frontend: session list + scaffolding (target: 0.5 day)

1. Vite + React + TS + Tailwind set up. Strict mode on.
2. shadcn/ui added with only Button, Card, Slider, Dialog.
3. SessionList component: fetches `/api/sessions` via TanStack Query,
   displays a list with robot_id, started_at (relative time), duration,
   label badge.
4. Click a session → routes to `/sessions/:id` (use react-router-dom v6).

Deliverable: Open http://localhost:5173, see the list, click into a
detail page (still empty).

### Phase 6 — Frontend: MCAP loader in Web Worker (target: 1.5 days)

1. `workers/mcap-decoder.ts`: a Web Worker that:
   - Receives a session URL via postMessage
   - Fetches the MCAP file using ranged requests (start with full file
     for v0; chunked streaming is v1)
   - Parses with `@mcap/core`
   - Sends back: schema/channel info, then a stream of decoded messages
     bucketed by topic, with timestamps in nanoseconds
2. Main thread: receives messages, builds in-memory data structures per
   topic. For v0, full in-memory load is fine since session is ≤60s.
3. Handle the four message types we care about:
   - `sensor_msgs/msg/CompressedImage` → base64 jpeg, will become a video
     track via canvas frame paint (no transcoding in v0)
   - `geometry_msgs/msg/Twist` → numeric chart
   - `tf2_msgs/msg/TFMessage` → pose track (display as text "x: y: z:" for v0,
     no 3D yet)
   - Anything else → ignore in v0

Deliverable: Open a session detail page; in the dev console, log
"loaded N messages across K topics" within 2 seconds.

### Phase 7 — Frontend: the timeline scrubber (target: 3 days, the heart of v0)

This is the part that has to feel good. Spend the time.

1. `Timeline.tsx`: PixiJS Application mounted in a fixed-height div.
   Manages:
   - Time axis (top): renders tick marks every 1s, labels every 5s
   - Tracks (stacked vertically): each track gets a fixed height
   - Playhead: vertical red line, draggable
   - Pan/zoom via wheel (zoom) + drag (pan)
2. `Playhead.ts`: stateful playhead time (in ns from session start).
   Source of truth lives in zustand `playback` store:
   ```ts
   interface PlaybackState {
     currentTimeNs: bigint
     durationNs: bigint
     isPlaying: boolean
     setTime: (t: bigint) => void
     play: () => void
     pause: () => void
   }
   ```
3. `TrackVideo.tsx`: receives decoded CompressedImage frames. Renders the
   nearest-by-timestamp frame to a Pixi sprite. When playhead moves,
   re-paint. Two of these stacked (front cam, rear cam) — even if there's
   only one camera in the test data, support up to two.
4. `TrackChart.tsx`: receives Twist data. Renders linear.x as a line
   chart on the timeline. Y-axis on the right edge.
5. `TrackPose.tsx`: receives TFMessage. Renders current x, y, yaw as
   text in a fixed panel (not on the timeline). v0 simplification.
6. Playback loop: requestAnimationFrame; when isPlaying, advance
   currentTimeNs by (deltaMs * 1e6 / playbackSpeed). Frame-rate
   independent.
7. Keyboard shortcuts: Space = play/pause, Left/Right = step 100ms,
   Shift+Left/Right = step 1s.

Deliverable: Open a session, see two video frames updating as you drag
the playhead, see the velocity chart with playhead overlay, see pose
text updating. Hit space, video plays, charts update, looks smooth.

### Phase 8 — Polish + dev experience (target: 0.5 day)

1. `scripts/dev.sh`: launches agent, backend, web in a tmux session
   with three panes. Uses your existing tmux config.
2. `scripts/seed-fixture.sh`: downloads a small public ROS 2 bag,
   converts to a session by replaying through the agent, so a new dev
   has data within 1 minute of clone.
3. README with: prerequisites (ROS 2 Humble or Jazzy, Python 3.11+,
   Node 20+, pnpm), 5-step quickstart.
4. Top-level `make dev`, `make test`, `make fmt`, `make lint`.

Deliverable: Fresh clone → `make dev` → working app with sample data in
under 5 minutes.

## Hard Rules — DO NOT DEVIATE

These exist because past mistakes are predictable. Follow them.

1. **No deserialization in the agent's hot path.** The ring buffer stores
   bytes. Decoding happens in the browser. This keeps agent CPU near zero.
2. **No frame transcoding in v0.** CompressedImage → display the JPEG as-is.
   Don't call into ffmpeg or libav. v1 problem.
3. **No background workers in the backend.** It's a read-only file server
   over sqlite. Do not introduce Celery, RQ, or async task queues.
4. **No state in the timeline component itself.** Playback state lives in
   zustand. The PixiJS canvas renders state; it doesn't own state. This
   prevents the entire class of "playhead is in two places" bugs.
5. **No premature abstraction over track types.** Write TrackVideo,
   TrackChart, TrackPose as separate components first. Extract a
   common interface only after the third one is written.
6. **No multi-robot.** If a feature requires distinguishing robots in the
   UI, it's out of scope.
7. **No auth, no users, no orgs.** v0 is single-tenant on localhost.
8. **No Docker for v0.** Run everything natively. Docker is a v1 concern.
9. **No CI for v0.** Pre-commit hooks are enough. CI when there are
   multiple contributors.
10. **Do not silently catch exceptions.** Every except clause logs at
    ERROR with full traceback. Crashes are better than silent data loss.

## Non-Functional Requirements

- Agent CPU usage during normal recording: <2% on a Jetson Orin Nano.
  If a code change pushes this above 5%, that change is wrong.
- Agent RAM: <200MB for a 60s buffer at typical topic rates.
- Session save latency (from POST to file written): <500ms for a
  60s/200MB buffer.
- Frontend timeline FPS: 60fps when scrubbing on a 2020 MacBook Air.
  If it drops below 45fps, profile and fix before adding features.
- MCAP files written by the agent must open without warnings in
  `mcap doctor` and in Foxglove Studio.

## Acceptance Test (must pass before v0 is "done")

1. Start ROS 2 with a recorded bag of a real robot drive
   (e.g., a Turtlebot3 in Gazebo with a deliberate 7s stop).
2. `make dev` starts agent + backend + web.
3. Replay the bag. Within 5 seconds of the stall, a new session appears
   in the web UI without manual intervention.
4. Click the session. Within 3 seconds, the timeline loads.
5. Drag the playhead to the moment of the stall. Video frames, velocity
   chart, and pose text all update in sync.
6. Press space. Playback runs at 1x in real time without dropping frames.
7. Open the same MCAP file in Foxglove Studio independently. It opens
   without errors and shows the same data.

If any of these 7 fail, v0 is not done.

## How to Use This Spec with Claude Code

1. Start a Claude Code session in the repo root.
2. Ask Claude to read SPEC.md.
3. For each phase, in order:
   - Say: "Implement Phase N. Stop when the deliverable is met. Do not
     start Phase N+1."
   - Review the diff. Run the deliverable test manually.
   - Commit before moving to the next phase.
4. If Claude proposes scope outside the phase's deliverable, push back:
   "That's a v1 concern. Stay within Phase N's deliverable."
5. If a phase deliverable fails its test, do not move forward. Fix it.

## Out of Scope (explicit v1 / v2 list, so we stop arguing about it)

**v1:** Rust agent rewrite, multi-robot, cloud ingestion, auth, RBAC,
Docker, CI, real-time live streaming, full 3D point cloud rendering,
ranged MCAP streaming, search across sessions, annotations, sharing
URLs, anomaly types beyond stall, video transcoding to HLS, mobile
responsive layout, dark mode, plugin system, MCAP import from existing
rosbags, comparison view (this run vs last run), customer billing,
Helm chart, Kubernetes deployment, MCP server integration, decision
trace beyond raw topic data.

**v2:** everything else.
