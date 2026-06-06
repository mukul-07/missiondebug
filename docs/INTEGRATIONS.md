# MissionDebug Integrations

MissionDebug captures session data when its built-in detectors fire — but
your monitoring stack already detects plenty of things the built-in
detectors don't. Point those external alerts at the agent's save
endpoint and you get root-cause replay for free.

The integration contract is dead simple:

```
POST http://<robot>:7000/sessions/save
Content-Type: application/json

{"label":"<your-label-here>"}
```

That's it. Any alerter, watchdog, or monitoring tool that can fire an
HTTP request can trigger a MissionDebug session.

This document covers four concrete recipes — three that *trigger* captures,
and one (OpenTelemetry) that *exports* incidents to your observability
stack. For the full API surface, see [`API.md`](./API.md).

---

## 1. Generic webhook (any monitoring tool, ~30 seconds)

If your monitoring tool can call a URL on alert, you're done.

```bash
curl -X POST http://localhost:7000/sessions/save \
  -H 'Content-Type: application/json' \
  -d '{"label":"my-alert-fired"}'
```

The label appears next to the session in the MissionDebug UI and in
`GET /api/sessions`, so engineers can find the captured 60s for that
specific alert.

### Use cases

- Bash watchdog scripts: `curl` on failure.
- Datadog, Grafana, New Relic, custom dashboards: configure a webhook
  receiver.
- Internal mission-software: one HTTP call, no SDK needed.

---

## 2. Prometheus Alertmanager (~5 minutes)

Alertmanager's webhook receivers send a fixed JSON shape (with
`alerts[]`, `commonLabels`, etc.) — not the `{label}` body the save
endpoint expects. Two ways to bridge:

### Option A: shell shim (simplest)

Use [`examples/integrations/alertmanager-shim.sh`](../examples/integrations/alertmanager-shim.sh)
behind a tiny `socat` or `systemd` socket-activated listener:

```bash
# Quick test (no production!): expose the shim on :7001
socat TCP-LISTEN:7001,fork,reuseaddr EXEC:./examples/integrations/alertmanager-shim.sh
```

Then in `alertmanager.yml`:

```yaml
receivers:
  - name: missiondebug
    webhook_configs:
      - url: http://localhost:7001
        send_resolved: false

route:
  receiver: missiondebug
  group_by: [alertname]
```

The shim extracts `alerts[0].labels.alertname` and forwards it as
`label="alertmanager:<alertname>"`.

### Option B: webhook template (Alertmanager 0.27+)

If you're on a recent Alertmanager, you can avoid the shim by writing
a custom template that POSTs the right body shape directly. See the
[Alertmanager webhook config docs](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config).

---

## 3. ros2_medkit Triggers (~10 minutes)

[ros2_medkit](https://github.com/selfpatch/ros2_medkit) is a live-ops
diagnostics gateway for ROS 2 — complementary to MissionDebug's
post-incident replay (see the [`How MissionDebug fits` table in the README](../README.md#how-missiondebug-fits)).
medkit exposes a [Trigger](https://selfpatch.github.io/ros2_medkit/tutorials/triggers-use-cases.html)
mechanism that lets you express conditions like *"fire when this entity's
status changes to failed"*.

medkit's triggers expose **Server-Sent Events (SSE)** rather than
outbound webhooks. We ship a small bridge script that subscribes to one
or more trigger event streams and forwards each event to MissionDebug.

### Setup

1. On your robot, define triggers in medkit for the conditions you want
   to capture (see medkit's
   [Triggers Use Cases tutorial](https://selfpatch.github.io/ros2_medkit/tutorials/triggers-use-cases.html)).
   Each trigger has an `event_source` URL like
   `/api/v1/apps/temp_sensor/triggers/trig_1/events`.

2. Run the bridge alongside the MissionDebug agent and medkit:

```bash
pip install requests sseclient-py

export MEDKIT_URL=http://localhost:8080
export MD_AGENT_URL=http://localhost:7000

python3 examples/integrations/medkit_bridge.py \
  /api/v1/apps/temp_sensor/triggers/trig_1/events \
  /api/v1/apps/lidar_driver/triggers/trig_3/events
```

3. When any of those triggers fires, the bridge POSTs to MissionDebug
   with a label like `medkit:api-v1-apps-temp_sensor-triggers-trig_1-events`.
   The captured 60-second session shows up in the UI labeled with which
   trigger produced it.

### Production deployment

For real deployments, run the bridge under systemd or supervisor with
auto-restart. It reconnects on stream errors with exponential backoff,
so transient network issues won't lose triggers.

### Why this combination

medkit answers *"what's wrong right now?"* — fault codes, parameters,
operations. MissionDebug answers *"what happened in the 60 seconds
before?"* — replay, scrub, annotate. A team running both:

- Gets a fault alert from medkit, immediately gets the pre-fault replay
  from MissionDebug.
- Doesn't have to choose between live diagnostics and post-incident
  forensics.
- Uses each tool for what it's specifically designed for.

---

## 4. OpenTelemetry — export incidents to your observability stack (~5 minutes)

Recipes 1–3 point *inbound* alerts at MissionDebug. This one goes the
other way: the **hub** exports its incidents and KPIs *outbound* as
standard OpenTelemetry, so they land in the Grafana / Datadog / on-call
setup your team already runs. MissionDebug stops being a separate tab and
becomes metrics + alerts inside your existing stack.

**Where it runs:** the hub emits — never the robot. The hub sends to an
OTLP collector *you* run on *your* network (Grafana Alloy, the OpenTelemetry
Collector, a Datadog agent, …). Nothing goes to a MissionDebug cloud, and
only incident *metadata* is exported (robot id, rule, subsystem, counts,
deep-link) — never MCAP bytes, camera frames, or PII. Works fully
air-gapped.

### Setup

Install the extra and point the hub at your collector:

```bash
pip install 'missiondebug-backend[otel]'

# OTLP/HTTP base URL of your collector (the hub appends /v1/metrics and /v1/logs)
export MD_OTEL_ENDPOINT=http://otel-collector:4318
export MD_OTEL_HEADERS="authorization=Bearer <token>"   # optional
export MD_OTEL_SERVICE_NAME=missiondebug                 # optional
export MD_HUB_PUBLIC_URL=https://hub.internal:8000       # for deep-links in events
```

Leave `MD_OTEL_ENDPOINT` unset and nothing is emitted — standalone /
air-gapped installs are unaffected (no extra dependency loads).

### What you get

**Metrics** (for Grafana panels + your own alert rules):

| Metric | Type | Meaning |
|---|---|---|
| `missiondebug.incidents.captured` | counter | incidents captured (attrs: robot_id, subsystem, rule) |
| `missiondebug.incidents.resolved` | counter | first transition to a terminal status (attr: status) |
| `missiondebug.agents.reporting` / `.total` | gauge | fleet operational health |
| `missiondebug.incidents.open` | gauge | open + investigating, last 30 days |
| `missiondebug.recurrence.rate` | gauge | fraction marked duplicate, last 30 days |
| `missiondebug.mttr.days` | gauge | mean time to first resolution, last 30 days |

**Events** (logs) — one structured record per captured incident, with a
deep-link back to the session and a "Nth occurrence of this pattern" hint.
Route these to Slack / PagerDuty with your collector's existing pipeline,
e.g. a message like:

```
WARN  Incident on warehouse-bot-03: battery_low — 3rd occurrence of this pattern.
      Auto-triggered by rule 'battery_low' … (subsystem: power).
      url=https://hub.internal:8000/sessions/SES-203
```

### Why this over a per-vendor webhook

It's vendor-neutral: the same export feeds Grafana, Datadog, Honeycomb,
and your alert routing without MissionDebug shipping (and you maintaining)
a connector per tool. Your on-call sees the robot incident the same way it
sees everything else — with the replay one click away.

---

## 5. Natural-language incident agent (~2 minutes, opt-in)

Ask the fleet's incident history in plain English instead of clicking
around — *"why does warehouse-bot-03 keep stalling, and what fixed it last
time?"* The hub runs an LLM agent with read-only tools over the incident
corpus (search / detail / similarity / fleet-stats) and answers with
grounded, session-id-cited responses.

**Enable it** (hub-side; the robot is uninvolved):

```bash
export MD_LLM_API_KEY=sk-ant-...      # Anthropic, or sk-... for OpenAI (auto-detected)
export MD_LLM_MODEL=...                # optional; defaults per provider
# air-gapped? point at a local / OpenAI-compatible model (Ollama, vLLM, …):
export MD_LLM_PROVIDER=openai
export MD_LLM_BASE_URL=http://your-local-llm:8080/v1
```

**Provider:** Anthropic and OpenAI-compatible are both supported. The key
prefix is sniffed automatically (`sk-ant-` → Anthropic, other `sk-` →
OpenAI); set `MD_LLM_PROVIDER=anthropic|openai` to be explicit. Because
OpenAI-compatible is also what local model servers speak, the same path
runs fully on-prem for air-gapped sites.

Leave `MD_LLM_API_KEY` unset and the agent reports `enabled: false` — every
other surface (dashboard, similarity, summaries) keeps working offline.

```
POST /api/v2/incidents/ask  { "question": "..." }
  -> { enabled, answer, citations: [session_id...], tools_used: [...] }
GET  /api/v2/incidents/agent  -> { enabled }
```

**What leaves the hub:** only incident *metadata* (ids, rule, subsystem,
timestamps, summary text, resolution text, counts) — never MCAP bytes,
camera frames, or PII. Use a local model (`MD_LLM_BASE_URL`) to keep
everything on-prem.

---

## Custom integrations

If you've wired MissionDebug into something not listed here — Sentry,
PagerDuty, a custom watchdog, your own ROS node — open a PR with a
short recipe. The point of this document is to grow with the patterns
the community actually uses.

For the full agent + backend API surface, see [`API.md`](./API.md).
