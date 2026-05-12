# MissionDebug v2 — Build Specification (Fleet Edition)

## Context

v1.5 proved the single-robot wedge: post-incident replay against a
60-second rolling buffer, MCAP-native, Foxglove-native, local-first.
It works. It's polished. It's ready to install.

But the customers who would actually pay for MissionDebug are running
10, 50, 100 robots. For them, "open MissionDebug for robot-47" is not
a workflow. The workflow is *"which 5 robots had the most anomalies
this week? Show me the worst one. Was the firmware update worse than
the baseline?"* — and v1.5 cannot answer any of those questions.

**v2 climbs MissionDebug from engineer-tier to fleet-tier** by adding
a single architectural shift — a central hub backend that ingests
session metadata from N agents on N robots — and the operational
surfaces that come with it (fleet observability, ops health, auth,
storage, alerting).

The engineer-tier wedge stays alive. The agent still runs standalone
on a single robot with no hub. The web UI still works against a
single backend. But the *same* binaries, with one config flag, also
work in a fleet deployment.

### Why now

Three signals, all from the last 30 days:

1. **A peer founder validated the product and offered distribution.**
   Christian Fritz (Transitive Robotics) confirmed MissionDebug is
   useful, offered to introduce his customer base (each with 10+ robots),
   and offered to list MissionDebug as a capability on the Transitive
   platform. His customers cannot use MissionDebug at fleet scale today.
2. **Five warm leads said they'd try it.** Re-engagement push is in
   flight. Their evaluation will surface fleet-vs-single-robot signal.
3. **The competitive frame is sharper.** ros2_medkit at 221 stars
   serves live-ops at fleet scale and explicitly disclaims replay.
   MissionDebug owns the replay wedge — but only if it can deliver at
   fleet scale where the budget lives.

v1.6 (rate_of_change + cross_topic rule kinds) is **deferred** to v2.1
or later. Rule kinds extend the engineer-tier surface; they do not
unlock fleet-tier adoption. Build them after a fleet customer asks.

### How the user actually uses it

Three deployment shapes, all supported from a single codebase:

1. **Single-robot, no hub.** Engineer tier. v1.5 unchanged. Agent +
   backend + web on one robot. No hub, no auth, no fleet view. The
   30-second Docker demo still works.
2. **Fleet, self-hosted hub.** Default fleet deployment. Agents on
   robots push session metadata to a hub running on the customer's
   own infrastructure. Hub UI shows all sessions across all robots.
3. **Fleet, Transitive capability.** Hub runs inside Transitive's
   cloud, agents inside their robot-side sandbox. Customer manages
   nothing. Capability listed publicly. This is the commercial path
   for teams without their own ops capacity.

## What v2 IS

- **Central hub backend.** Same FastAPI codebase, deployed once
  centrally. Ingests session metadata from N agents. Stores its own
  SQLite (or Postgres at fleet scale). Serves a fleet-aware web UI.
- **Agent → hub sync.** Optional `--hub-url` flag on the agent.
  Pushes session metadata (id, robot_id, label, started_at, topics,
  mcap_size_bytes, the local filesystem path) to the hub each time it
  saves a session. MCAP bytes stay on the robot by default; hub pulls
  on demand when a user opens a session.
- **Multi-robot session list.** Hub UI shows every session from every
  reporting robot. Filter by robot_id, label, time range. Free-text
  search over labels.
- **Fleet observability dashboard.** Counts (today / 7d / 30d),
  per-robot trend lines, outlier highlights ("robot-007 has 3x the
  anomaly rate of its peers this week"). Single screen the CTO opens
  Monday morning.
- **Operational health.** Each agent posts a heartbeat to the hub
  every 60s. Hub knows which robots are silent. Surfaces "94/100
  agents reporting in the last hour, 6 unhealthy: [list]." Alerts
  when an agent stops reporting.
- **Auth.** Single shared password (basic auth) by default — enough
  to pass a CISO review. Optional OIDC / SAML for enterprise.
- **Pluggable MCAP storage.** Default: stays on the robot, hub
  fetches via the agent's existing `GET /api/sessions/{id}/mcap` on
  demand. Optional: agent uploads MCAP to an S3-compatible object
  store; hub fetches from there instead. Lifecycle policies for cold
  storage after N days.
- **Alerting.** Hub fires webhooks when configured anomalies occur
  or when fleet-health changes. PagerDuty / Slack / generic webhook
  recipes shipped, mirroring the v1.5 ingest pattern in reverse.
- **RBAC (three roles).** viewer, editor, admin. Sufficient for
  small-to-mid robotics teams. No team management, no audit log —
  those are v2.5+.

## What v2 IS NOT

- Not a replacement for the v1.5 engineer-tier deployment. Standalone
  install path (`.deb`s, Docker demo) stays exactly as it is.
- Not auto-upload of MCAPs to the hub. MCAPs are large and often
  sensitive. Default behavior: they stay on the robot. Customers
  opt in to S3 upload explicitly.
- Not cloud-only. MissionDebug remains **self-hostable end-to-end**.
  Customers in defense / hospital / industrial settings can run the
  hub on-prem with no cloud dependency.
- Not live streaming. v3+ territory. Hub shows captured sessions; it
  does not tail the agent's live buffer.
- Not multi-cluster / hub-of-hubs federation. One hub serves one
  fleet. Customers with 10,000+ robots are v3.
- Not a marketplace, plugin system, ML-based detector, or
  comparison/diff view. All deferred to v2.5+ pending real customer ask.
- Not native mobile. The web UI works on a phone but is not
  responsive-optimized.
- Not SOC2-attested. Compliance work begins after the first paying
  fleet customer; v2 ships unattested.

## Tech Stack (delta from v1.5)

Everything in v1.5 stays.

### Agent
- New optional config: `hub_url`, `hub_auth_token`,
  `heartbeat_interval_seconds`.
- New module `hub_client.py` — posts session metadata + heartbeats.
  Pure-Python, no rclpy, testable in isolation. Failures are
  non-fatal (agent keeps working without hub).

### Hub backend
- Same FastAPI codebase as v1.5 backend, plus:
  - New routes: `POST /api/v1/agents/heartbeat`,
    `POST /api/v1/sessions/ingest`,
    `GET /api/v1/agents/health`,
    `GET /api/v1/fleet/stats`.
  - New SQLite tables: `agents`, `agent_heartbeats`.
  - Postgres backend option for fleets >50 robots (SQLite still works
    for smaller deployments; the schema and queries stay portable).
  - Auth middleware (basic auth + optional OIDC).
  - Webhook dispatcher for alerts.

### Web UI
- Fleet session list view (the existing list, but no longer scoped to
  a single robot's sessions).
- Fleet stats dashboard (new page).
- Agent health page (new page).
- Auth-aware (login screen, session cookie, logout).
- No new visualization components — single-session detail view is
  unchanged from v1.5.

### Packaging
- Two new `.deb`s: `missiondebug-hub`, `missiondebug-hub-postgres`
  (latter wraps the same hub backend with Postgres deps preinstalled).
- Updated agent `.deb` accepts the new `hub_url` config.
- Hub container image published to GHCR (separate from agent image).
- Transitive capability bundle (`robot/`, `cloud/`, `web/` shape per
  their SDK) — built as part of P7 once the listing is real.

### Storage
- New abstraction `MCAPStore` with two implementations:
  `RobotLocalStore` (fetches from the agent on demand) and
  `S3Store` (presigned URLs to an S3-compatible bucket).
- Lifecycle policy config: `cold_after_days`, `delete_after_days`.

## Repository Layout (delta from v1.5)

```
missiondebug/
├── v2-SPEC.md                                # this file
├── agent/src/missiondebug_agent/
│   └── hub_client.py                         # NEW: posts to hub
├── backend/src/missiondebug_backend/
│   ├── auth.py                               # NEW: basic + OIDC
│   ├── storage.py                            # NEW: MCAPStore abstraction
│   ├── webhooks.py                           # NEW: alert dispatcher
│   └── routes/
│       ├── agents.py                         # NEW: heartbeat + health
│       ├── fleet.py                          # NEW: stats endpoints
│       └── ingest.py                         # NEW: session metadata ingest
├── web/src/
│   ├── pages/FleetStats.tsx                  # NEW
│   ├── pages/AgentHealth.tsx                 # NEW
│   ├── pages/Login.tsx                       # NEW
│   └── components/RobotFilter.tsx            # MODIFIED: now fleet-aware
├── packaging/debian/
│   ├── hub/                                  # NEW: hub .deb skeleton
│   └── hub-postgres/                         # NEW: postgres variant
├── transitive-capability/                    # NEW: P7, npm package shape
│   ├── robot/
│   ├── cloud/
│   └── web/
└── docs/
    ├── DEPLOYMENT_FLEET.md                   # NEW: hub deployment guide
    ├── DEPLOYMENT_TRANSITIVE.md              # NEW: capability install
    └── ARCHITECTURE.md                       # NEW: hub + agent diagram
```

## Build Plan — Phased

### Phase 1 — Central hub backend (target: 4 weeks)

The architectural keystone. Everything else depends on this.

1. Schema additions: `agents` table (one row per robot reporting in),
   `agent_heartbeats` table (TTL-trimmed). Existing `sessions` table
   gains a `mcap_url` column (where to fetch the MCAP from — local
   path on robot, or s3:// URL).
2. New routes:
   - `POST /api/v1/sessions/ingest` — agent posts session metadata.
   - `POST /api/v1/agents/heartbeat` — agent liveness ping every 60s.
3. New `hub_client.py` in the agent. Background task that posts
   metadata after each session save + heartbeats every 60s. Backoff
   on network errors. Non-fatal failures.
4. Hub backend: when a UI user opens a session, fetch the MCAP from
   the agent's existing `GET /api/sessions/{id}/mcap` endpoint and
   stream-proxy to the browser. Cache aggressively.
5. Tests:
   - Agent posts session metadata after save; hub indexes it.
   - Agent loses network connection; reconnects; backfill is not
     required (sessions written during disconnect are visible after
     reconnect via next ingest).
   - Hub fetches MCAP from agent; range requests work end-to-end.
   - Multiple agents reporting to one hub; sessions list shows all,
     filterable by robot_id.

**Deliverable:** Two robots running agents, one hub on a third machine.
Both robots' sessions visible in the hub UI. Click a session, scrub
it. Tested in CI with two agent instances + one hub.

### Phase 2 — Operational health (target: 1 week)

1. Hub computes "expected vs reporting" agents based on heartbeat
   recency. Configurable timeout (default 5 minutes).
2. New `/api/v1/agents/health` endpoint: list of agents with
   `last_heartbeat`, `status: healthy|stale|silent`, `agent_version`.
3. New `AgentHealth.tsx` page in the web UI. Sorted by silence
   duration, descending. "94 of 100 agents reporting; 6 unhealthy."
4. Tests:
   - Heartbeat timeout transitions agent to `stale` after configured
     interval.
   - Recovery: stale agent posting a heartbeat returns to `healthy`.

**Deliverable:** Kill an agent, hub reports it as `silent` within 5
minutes. Restart it, hub reports `healthy` within 60s.

### Phase 3 — Fleet observability dashboard (target: 2 weeks)

1. New routes:
   - `GET /api/v1/fleet/stats` — counts and trends.
   - `GET /api/v1/fleet/outliers` — robots with elevated anomaly
     rates relative to fleet baseline.
2. New `FleetStats.tsx` page: counts (today / 7d / 30d), trend
   line, outlier highlight cards.
3. Aggregation is computed on read for v2; precomputed materialized
   views are v2.5 territory.
4. Tests:
   - Stats endpoints return correct counts against seeded data.
   - Outlier detection flags robots with >2× the fleet median
     anomaly rate over a configurable window.

**Deliverable:** With 100 seeded sessions across 10 robots, the
dashboard shows accurate counts and identifies the outlier.

### Phase 4 — Auth (target: 1 week)

1. Basic auth middleware. Single shared password from env
   (`MD_HUB_AUTH_PASSWORD`). All routes except `/healthz` gated.
2. Login screen on the web UI. Sets a session cookie. Logout.
3. Agent `--hub-auth-token` accepted as bearer for ingest +
   heartbeat routes.
4. Tests:
   - Unauthenticated requests to gated routes return 401.
   - Wrong password rejected.
   - Logout clears the session.
   - Agent with valid token can ingest sessions.

**Deliverable:** Hub UI requires login. Curl to gated routes without
auth returns 401. CISO checkbox satisfied.

### Phase 5 — Pluggable storage (target: 2 weeks)

1. `MCAPStore` abstraction with two implementations.
2. `RobotLocalStore` (default, mirrors v1.5 behavior): hub fetches
   from agent on demand. Already works after P1.
3. `S3Store`: agent uploads MCAP to a configured S3-compatible bucket
   after save; hub fetches via presigned URL. Compatible with AWS S3,
   MinIO, R2, GCS-via-S3-interop.
4. Lifecycle policies: `cold_after_days` (move to glacier-tier),
   `delete_after_days` (purge).
5. Tests:
   - S3 upload happens after session save; hub fetch works.
   - Network failures during upload retry with backoff; session
     metadata still ingests with `mcap_url=pending` and the upload
     completes asynchronously.
   - Lifecycle: insert old sessions; sweeper moves/deletes as configured.

**Deliverable:** Fleet of 10 robots, all configured to upload to MinIO.
30 days of sessions accumulate; lifecycle policy correctly tiers and
purges.

### Phase 6 — Alerting (target: 1 week)

1. Webhook dispatcher in the hub. Configurable destinations.
2. Trigger conditions: anomaly fires, agent goes silent, fleet
   anomaly rate spikes.
3. Recipes shipped: PagerDuty, Slack, generic webhook (mirrors
   `docs/INTEGRATIONS.md` shape).
4. Tests:
   - Mock webhook endpoint receives correct payload on each trigger.
   - Failure to deliver a webhook does not block other deliveries.
   - Rate-limiting: at most one notification per condition per
     configurable cooldown window.

**Deliverable:** Configure Slack webhook, fire a detector, message
appears in Slack with deep-link back to the session.

### Phase 7 — SSO + RBAC + Transitive capability bundle (target: 3 weeks)

1. OIDC/SAML support, replacing basic auth for enterprise customers.
   Auth0, Okta, Azure AD tested.
2. RBAC with three roles: `viewer`, `editor` (annotations), `admin`
   (config + retention + agent management). Stored as a role claim.
3. Transitive capability bundle: package the agent (robot/), hub
   (cloud/), and web (web/) into Transitive's npm SDK shape. Open
   question from v1.5-era — can rclpy run inside their Node sandbox —
   gets resolved here. If yes, capability lists publicly. If no, this
   phase scope shrinks to "the bundle exists but is unpublished" and
   we revisit.
4. Tests:
   - OIDC login flow with Auth0; role from claim is enforced.
   - Editor cannot delete sessions; admin can.
   - Transitive capability bundle installs cleanly via
     `transitive-cli install`.

**Deliverable:** A Transitive customer can install MissionDebug as a
capability on their fleet in <10 minutes via their existing portal,
authenticate via their existing SSO, and see fleet sessions immediately.

## Hard Rules — DO NOT DEVIATE

The v0 ten + v1's three + v1.5's three + v1.6's one still apply. Five
new ones:

18. **The agent must remain standalone-capable.** Every fleet feature
    is additive. Single-robot deployments without a hub must continue
    to work unchanged. The engineer-tier wedge is what makes the
    fleet-tier product trustable.
19. **MCAPs do not auto-upload by default.** Hub indexes metadata;
    MCAP transfer is on-demand or explicitly opt-in to S3. Robots
    with bandwidth or compliance constraints stay safe out of the box.
20. **The hub is self-hostable end-to-end.** No mandatory cloud
    dependency, no required SaaS account, no telemetry. Customers
    can run MissionDebug Fleet on an air-gapped network if they
    choose. The Transitive-hosted path is one option, not the only one.
21. **Auth defaults to enabled in fleet mode.** A hub with no
    `MD_HUB_AUTH_PASSWORD` set refuses to start. The v1.5 "network
    trust" stance only applies to single-robot mode.
22. **No write operations on robots from the hub.** The hub is
    read-only with respect to robot state. It cannot trigger captures,
    delete files, restart agents, or push configs. Those are
    operational concerns that belong to ros2_medkit or similar
    live-ops tools, not to a forensic replay product. Crossing this
    line dilutes the wedge.

## Non-Functional Requirements (delta from v1.5)

- **Hub scale target:** 100 robots, 30 days of retained sessions per
  robot (~3000 active sessions), <500ms p95 for session list queries,
  <2s p95 for fleet stats queries.
- **Agent overhead from hub sync:** <0.1% CPU, <10MB RAM additional.
- **Heartbeat traffic:** ~200 bytes per agent per 60 seconds. 100
  robots = ~20KB/min sustained inbound to the hub. Trivial.
- **MCAP fetch latency from robot via hub:** <2× direct fetch.
  Streaming proxy, not buffering.
- **Hub deployment footprint:** single Docker container (or `.deb`)
  fits in 1 vCPU + 2 GB RAM for ≤25 robots. 4 vCPU + 8 GB RAM up to
  100 robots on SQLite; Postgres backend above that.

## Acceptance Test (must pass before v2 is "done")

Same shape as v1.5: technical half + field half.

### Technical
1. 10 simulated agents push metadata to one hub for 7 days under
   synthetic load. No data loss, no hub crashes, no agent OOMs.
2. All v1.5 tests still green. New tests for ingest, heartbeat, auth,
   storage, alerting all green.
3. Fresh-install flow: hub `.deb` on Ubuntu 24.04, agent `.deb` on
   two robots, `MD_HUB_URL` configured. Sessions from both robots
   visible in hub UI in <60 seconds from install start.
4. Soak test: 30 days of 10-agent synthetic load. Disk retention
   keeps storage under cap. Heartbeat absences correctly transition
   agents to `silent`. Webhook alerts fire as expected.

### Field
5. **One real customer deploys MissionDebug Fleet on ≥10 robots for
   30 days.** Christian-introduced is the expected path; any other
   customer with comparable fleet size counts. Customer's own
   topics, own rules, own ops integrations.
6. Day-30 verdict from the customer:
   - **"This is in our debug stack."** v2 is done. Pricing
     conversation begins.
   - **"It works but we'd want X before deploying to more robots."**
     X is v2.0.5 scope; iterate. v2 is done in spirit.
   - **"It crashed / lost data / our CISO blocked it."** v2 has a
     bug. Fix and re-run.

The verdict to *avoid*: *"this would be great if it also did live ops."*
That's a sign the customer was pushed toward live-ops territory when
the product is post-incident. Pull back. Live ops is medkit's job.

## Out of Scope (explicit v2.5+ list)

Carries forward from v1.5's v2 list, plus:

- Live streaming / tail view (v3)
- Multi-cluster federation / hub-of-hubs (v3)
- Native mobile app (v3)
- ML-based anomaly detection (v3+)
- Marketplace for community-shared rule packs
- Comparison view between two sessions
- Audit log of who viewed what session
- Time-series metric extraction from MCAP fields
- Foxglove Studio deep integration
- Real-time collaboration (multiple engineers on one session)
- Rule kinds beyond v1.6's `rate_of_change` and `cross_topic`
- SOC2 attestation (begins after first paying customer, not in v2)

## How to Use This Spec

Same as v0/v1/v1.5:

1. Implement Phase N. Stop at the deliverable. Commit.
2. Don't start N+1 until N's test passes.
3. Reject in-phase scope creep with: "that's v2.5."

If a design partner asks during the cycle for something not in this
spec, write it down — that's v2.0.5 input — but don't insert it. The
30-day fleet trial needs a fixed target.

## What this spec costs

~12-14 working weeks of focused engineering, real-time. Larger than
any prior version. v2 is genuinely a different product shape — the
biggest delta since v0 → v1.

Risk-managed by phase discipline: **Phase 1 is the only one that
matters before customer validation.** If after Phase 1 + a 30-day
pilot with one Christian-introduced customer the fleet thesis is
wrong, the engineering cost was 4 weeks, not 14, and the agent's
standalone path is unaffected — engineer-tier customers continue to
get value from v1.5 unchanged.

If v2 lands and a real fleet customer says yes, **the product is
no longer "post-incident replay for a single robot" — it is "the
post-incident replay layer of the robotics ops stack."** Pricing
conversations begin in the $10-15/robot/month range; capability
listing on Transitive becomes a live distribution channel; SOC2
groundwork starts. v2.1 and beyond shape themselves around what
that customer asks for next.
