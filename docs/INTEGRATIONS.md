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

This document covers three concrete recipes. For the full API surface,
see [`API.md`](./API.md).

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

## Custom integrations

If you've wired MissionDebug into something not listed here — Sentry,
PagerDuty, a custom watchdog, your own ROS node — open a PR with a
short recipe. The point of this document is to grow with the patterns
the community actually uses.

For the full agent + backend API surface, see [`API.md`](./API.md).
