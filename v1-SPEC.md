# MissionDebug v1 — Build Specification

## Context

v0 proved the record → detect → replay loop works on one robot, on one
laptop, with one anomaly type. v1's job is to make that loop **usable
by a real robotics engineer at a real customer**, on their actual
robot, for at least one month, without you in the room.

v1 has ONE goal: validate that the design partner finds enough value to
keep using it (and ideally to pay for it). If v1 doesn't earn a "yes,
we'd keep this," the product idea is wrong and we change course before
v2.

## What v1 IS

- An installable agent (`.deb` package) that runs as a systemd service
  on the robot, starts at boot, survives reboots
- One additional anomaly detector beyond stall: **path deviation**
  (robot's pose drifts >X meters from its planned path)
- Per-robot identity surfaced through the entire stack
  (config → MCAP metadata → backend → web)
- Annotations on sessions: an engineer scrubs to a moment, types a
  note, saves it; notes appear in the session list
- Shareable URLs that deep-link to a specific session AND a specific
  timestamp inside it (`?t=23.4`)
- A real fixture bag committed to the repo so a fresh clone shows
  meaningful data inside 60 seconds, no ROS install needed for first impressions

## What v1 IS NOT

- Not multi-robot. UI still shows one robot at a time. (Customer
  navigates per-robot URLs.) Fleet view is v1.5.
- Not cloud-hosted. Still localhost / on-robot only.
- Not authenticated. No login, no users, no orgs. Network-level
  trust is enough for the design-partner stage.
- Not real-time live streaming. Replay only.
- Not 3D point cloud / URDF rendering. Same flat 2D viz as v0.
- Not Rust. Python agent stays. Rust comes when CPU pressure becomes a
  real number from a real customer.
- Not search across sessions. Labels + scroll is enough below ~500
  sessions. Search comes when a customer actually asks for it twice.
- Not Docker / Kubernetes / Helm. The .deb is the deployment unit.
- Not CI. Pre-commit hooks remain enough.
- Not new anomaly types beyond path-deviation. One new detector
  is enough to prove the framework generalizes; more come per
  customer ask.

If a feature is not explicitly listed in "v1 IS", it does not exist
in v1. Reject scope creep aggressively. Same rule as v0.

## Tech Stack (delta from v0)

Everything in v0 stays. Additions:

### Agent
- `dpkg-buildpackage` toolchain for building the .deb
- systemd unit file (`/lib/systemd/system/missiondebug-agent.service`)
- `nav_msgs/msg/Path` subscription type added to the schema map (for
  path-deviation detector)

### Backend
- New table: `annotations` (one row per note)
- New endpoints: `POST /api/sessions/{id}/annotations`,
  `GET /api/sessions/{id}/annotations`,
  `DELETE /api/annotations/{id}`

### Frontend
- URL state for `?t=<seconds>` (parse on load, set playhead; update on
  drag, debounced)
- Annotation pin overlay on the timeline (small dots at annotation
  timestamps; click → side panel)
- Annotation side panel: list, create, delete
- Per-robot color/badge in session list

### Tooling
- One real fixture bag at `fixtures/sample_drive.mcap` — committed in
  Git LFS or as a download script depending on size

## Repository Layout (delta from v0)

```
missiondebug/
├── ...                            # everything from v0 stays
├── v1-SPEC.md                     # this file
├── packaging/
│   └── debian/
│       ├── changelog
│       ├── control
│       ├── rules
│       ├── missiondebug-agent.service     # systemd unit
│       └── postinst                       # creates user, dirs, enables service
├── fixtures/
│   ├── sample_drive.mcap          # real bag with /tf, /cmd_vel, /camera, /plan
│   └── README.md                  # provenance
├── agent/src/missiondebug_agent/
│   └── detectors/
│       ├── __init__.py
│       ├── stall.py               # moved from anomaly.py
│       └── path_deviation.py      # NEW
└── web/src/components/timeline/
    └── AnnotationLayer.tsx        # NEW
```

## Build Plan — Phased

### Phase 1 — Path-deviation detector (target: 2 days)

1. Refactor `anomaly.py` → `detectors/stall.py`. No behavior change.
2. New `detectors/path_deviation.py`:
   - Subscribes to `/plan` (`nav_msgs/msg/Path`) and `/tf` (or a
     specific pose topic; configurable)
   - Maintains the latest plan; on each pose update, computes
     perpendicular distance to nearest segment
   - Fires anomaly if distance > `threshold_meters` for >= `duration_seconds`
3. Config schema additions:
   ```yaml
   anomaly:
     stall: { ... }
     path_deviation:
       threshold_meters: 0.5
       duration_seconds: 2.0
       cooldown_seconds: 30.0
   ```
4. Unit tests: nominal-on-path, drift-and-recover, drift-and-fire,
   plan-change-resets, cooldown.

Deliverable: Replay a bag with a deliberate 3-meter detour; auto-save
fires with label `anomaly:path-deviation`.

### Phase 2 — Per-robot identity through the stack (target: 1 day)

1. Agent: include `robot_id` as MCAP file-level metadata (already in
   filename; also write to MCAP `metadata` records).
2. Backend: extract `robot_id` from MCAP metadata when scanning;
   `GET /api/sessions?robot_id=foo` filter.
3. Web: small badge per session showing robot_id; URL filter
   `/?robot=robot-001`.

Deliverable: Two agents on different robot IDs writing to the same
sessions dir produce a unified UI list with badges + filterable.

### Phase 3 — Annotations (target: 2 days)

1. Backend: `annotations` table (id, session_id, time_ns, body, created_at).
   POST/GET/DELETE endpoints.
2. Web `AnnotationLayer.tsx`: pins on the timeline at each annotation's
   `time_ns`; tooltip on hover.
3. Side panel: list, create at current playhead, delete. Form is one
   textarea + Save button.
4. Annotation count appears in the session list row (e.g. "📝 2").

Deliverable: Drag playhead → click "Add note" → type → Save → pin
appears on timeline → reload page → pin still there → click pin →
side panel scrolls to that annotation.

### Phase 4 — Shareable URLs with timestamp (target: 0.5 day)

1. Web: parse `?t=<seconds>` on load, set initial playhead.
2. While playhead moves (debounced 250ms), update `?t=` via
   `history.replaceState`.
3. Copy-link button in the header: `navigator.clipboard.writeText`.

Deliverable: Open session, scrub to t=23.4s, copy URL, paste in new
tab → page opens at exactly 23.4s.

### Phase 5 — systemd packaging (target: 2 days)

1. `packaging/debian/` skeleton via `dh_make`.
2. `control`: depends on `python3.10`, `python3-rclpy`, `python3-fastapi`.
   Targets ROS 2 Humble on Ubuntu 22.04 arm64 + amd64.
3. `postinst`: creates `missiondebug` system user, `/var/lib/missiondebug/sessions/`,
   `/etc/missiondebug/config.yaml` (templated), enables the systemd unit.
4. `missiondebug-agent.service`:
   ```
   [Service]
   Type=simple
   User=missiondebug
   ExecStart=/opt/missiondebug/bin/agent
   Restart=on-failure
   ...
   ```
5. CI-less release: a `make package` target that produces a `.deb` in `dist/`.

Deliverable: On a fresh Ubuntu 22.04 VM:
`sudo dpkg -i dist/missiondebug-agent_1.0.0_arm64.deb`
→ agent running as a service inside 30 seconds, surviving reboot,
saving sessions to `/var/lib/missiondebug/sessions/`.

### Phase 6 — Real fixture bag + onboarding polish (target: 1 day)

1. Source one real ROS 2 bag (Turtlebot3 in Gazebo with a deliberate
   stall and a deliberate path detour) ~30s long, ~50MB.
2. Convert to a session MCAP via the agent (so the file is in
   MissionDebug's exact format) and commit to `fixtures/`. If too
   large for Git, add `scripts/download-fixture.sh`.
3. Backend: scan `fixtures/` on startup if `MD_FIXTURES=1`.
4. README: replace abstract "POST /sessions/save" with "open the
   sample session, drag the playhead, see the stall."

Deliverable: `git clone && make install && make dev` (no ROS install
required) → web UI shows the fixture session → click → timeline
shows real video and a real velocity drop.

## Hard Rules — DO NOT DEVIATE

The v0 ten still apply. Three new ones:

11. **Annotations are session-scoped, not user-scoped.** v1 has no
    users. Don't add an `author_id` column "for later" — that's
    speculative auth scope. Add it when v2 adds users.
12. **The .deb installs to `/opt/missiondebug` and `/var/lib/missiondebug`.**
    Don't sprinkle files across `/usr/local/`, `/etc/init.d/`, etc.
    One install dir, one data dir. Easy to uninstall.
13. **Detectors live in `detectors/` and are independent.** Each is
    one file, one class, one `update()` method, one callback. Don't
    pre-build a "DetectorRegistry" abstraction until there are 4+
    detectors.

## Non-Functional Requirements

- Agent CPU during recording: still <2% on a Jetson Orin Nano with
  three subscribed topics (carries over from v0).
- The .deb installs cleanly on a fresh Ubuntu 22.04 robot in <60s,
  no internet beyond apt mirrors.
- Annotation save latency: <100ms perceived (POST → pin appears).
- URL deep-link load: timeline ready at the requested timestamp in
  <2s on a 200MB session.

## Acceptance Test (must pass before v1 is "done")

The test is one customer. Concretely:

1. Pick **one** real robotics team. Give them v1 on a `.deb` and
   a 30-min onboarding call.
2. They install on **at least one production-like robot**.
3. **They use it independently for 30 days.** No engineering hand-holding.
4. At day 30, they answer: "would you keep using this?"
   - **Yes** → v1 is done. v1.5 scope comes from their feedback.
   - **No, but here's what's missing** → that feedback is more
     valuable than any feature you would have built. v1 is still
     done; v1.5 is whatever they said.
   - **Crickets** (didn't use it) → product idea is probably wrong.
     Don't build v1.5 yet — figure out *why* nobody used a tool that
     should have helped them.

The technical acceptance criteria (a green build, the .deb installs,
the annotations save) are necessary but not sufficient. **The customer
verdict is the test.**

## Out of Scope (explicit v1.5 / v2 list)

**v1.5** (next iteration after design-partner feedback):
fleet/multi-robot view, search across sessions, more anomaly types
on customer ask, optional cloud upload of selected sessions, basic
auth (single shared password to gate the UI), Foxglove-link export.

**v2:** Rust agent, full 3D viz, real-time live streaming, plugin
system for customer detectors, MCP server integration, Helm chart,
proper RBAC + multi-tenant cloud, mobile responsive UI, comparison
view, video transcoding to HLS, customer billing.

## How to Use This Spec

Same as v0:
1. Implement Phase N. Stop at the deliverable. Commit.
2. Don't start Phase N+1 until N's deliverable test passes.
3. Reject in-phase scope creep with: "that's v1.5."

Resist the urge to ship v1 with the design partner stuff *plus* "and
also fleet view, since it's only a few days." Adding features past
the original list is how v0 schedules slip into v1.5 schedules. The
30-day customer trial is the gate — keep moving toward it.
