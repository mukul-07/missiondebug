# MissionDebug HTTP API

Two FastAPI services, two API surfaces:

| Service | Port | Purpose |
|---|---|---|
| **Agent** (`missiondebug-agent`) | `127.0.0.1:7000` | Local control plane on the robot. Trigger session captures. |
| **Backend** (`missiondebug-backend`) | `0.0.0.0:8000` | Session index, file serving, annotations, retention. |

Both expose interactive Swagger UI at `/docs` and the raw OpenAPI spec at `/openapi.json`.

```bash
# Open the docs in a browser
open http://<robot>:7000/docs        # agent
open http://<robot>:8000/docs        # backend
```

## Authentication

Auth model depends on which mode the backend runs in:

| Mode | When auth applies | What's required |
|---|---|---|
| **`single`** (v1.5 default) | Only if `MD_HUB_AUTH_PASSWORD` is set | Opt-in. Network trust assumed otherwise. |
| **`fleet`** (v2 hub) | Always | `MD_HUB_AUTH_PASSWORD` is required at startup — hub refuses to start without it (Hard Rule 21). |

When auth is enabled, the hub gates every `/api/*` route via two paths:

- **Browser users**: HTTP Basic Auth. The browser prompts on first visit. Username is ignored; password must equal `MD_HUB_AUTH_PASSWORD`.
- **Agents** (robot → hub): `Authorization: Bearer <token>` header. Token equals `MD_HUB_AUTH_TOKEN`, which defaults to the password when unset — set both for independent rotation.

Always public regardless of mode: `/healthz`, `/openapi.json`, `/docs`, SPA static files.

```bash
# Agent posts a heartbeat with a Bearer token:
curl -X POST http://hub:8000/api/v1/agents/heartbeat \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"robot_id":"robot-001"}'

# Or use HTTP Basic in a script:
curl -u :hunter2 http://hub:8000/api/sessions
```

See [`SECURITY.md`](../SECURITY.md) for the threat model.

---

## Agent (`:7000`)

The agent runs on the robot and writes MCAP files when detectors fire. It exposes one capture endpoint plus a health probe.

| Method | Path | Summary |
|---|---|---|
| GET  | `/healthz` | Liveness + buffer status |
| POST | `/sessions/save` | Capture the current buffer as a new session |

### `POST /sessions/save`

Flush the in-memory 60-second rolling buffer to a new MCAP file and return the session metadata.

Use this when:
- An external monitoring system (Prometheus Alertmanager, your own watchdog, ros2_medkit Triggers) wants to capture state because *it* detected something the agent's built-in rules don't cover.
- An engineer is at a console and wants to grab "the last minute" manually because something looked off.

```bash
curl -X POST http://<robot>:7000/sessions/save \
  -H 'Content-Type: application/json' \
  -d '{"label":"weird-behavior-after-corner"}'
```

Request body (optional):

```json
{ "label": "string" }
```

Response (200):

```json
{
  "session_id": "fixture-robot_20260512T084230Z",
  "path": "/var/lib/missiondebug/sessions/fixture-robot_20260512T084230Z.mcap",
  "duration_s": 60.0,
  "topics": ["/cmd_vel", "/odom", "/tf"],
  "size_bytes": 1843200,
  "label": "weird-behavior-after-corner"
}
```

Returns **409** if the buffer is empty (agent just started, no messages yet).

### `GET /healthz`

```bash
curl http://<robot>:7000/healthz
# {"ok": true, "buffer_size": 1247, "robot_id": "my-robot"}
```

---

## Backend (`:8000`)

The backend indexes captured MCAP files, serves them with HTTP Range, and stores annotations. It's what the web UI talks to.

### Sessions

| Method | Path | Summary |
|---|---|---|
| GET | `/api/sessions` | List sessions (newest first, paginated) |
| GET | `/api/sessions/{session_id}` | Get a single session |

```bash
# List the 10 most recent
curl 'http://<robot>:8000/api/sessions?limit=10'

# Filter by robot
curl 'http://<robot>:8000/api/sessions?robot_id=my-robot'

# Get one session
curl http://<robot>:8000/api/sessions/sample_drive
```

**Fleet-readiness.** Each session carries a `robot_id` (set in the agent's
config). The list response also returns `robots: [...]` — the set of
distinct `robot_id`s the backend has indexed. The UI uses this for a
per-robot filter; integrations can use it to route sessions to per-robot
storage or alerting. Single-robot deployments today see "1 robot"; fleet
operators see the actual count without any extra configuration.

### Files

| Method | Path | Summary |
|---|---|---|
| GET | `/api/sessions/{session_id}/mcap` | Stream the MCAP bytes (Range-aware) |

```bash
# Download the full MCAP
curl -o session.mcap http://<robot>:8000/api/sessions/sample_drive/mcap

# Fetch a specific byte range (used by the browser scrubber)
curl -H 'Range: bytes=0-65535' http://<robot>:8000/api/sessions/sample_drive/mcap
```

### Annotations

| Method | Path | Summary |
|---|---|---|
| GET    | `/api/sessions/{session_id}/annotations` | List annotations for a session |
| POST   | `/api/sessions/{session_id}/annotations` | Create an annotation at a timestamp |
| PUT    | `/api/annotations/{annotation_id}` | Update an annotation's body |
| DELETE | `/api/annotations/{annotation_id}` | Delete an annotation |

```bash
# Add an annotation at t=23.4s
curl -X POST http://<robot>:8000/api/sessions/sample_drive/annotations \
  -H 'Content-Type: application/json' \
  -d '{"time_ns": 23400000000, "body": "Lidar drops out here"}'

# List them
curl http://<robot>:8000/api/sessions/sample_drive/annotations
```

### Admin

| Method | Path | Summary |
|---|---|---|
| GET  | `/api/admin/disk` | Disk usage + retention cap |
| POST | `/api/admin/rescan` | Rescan the sessions directory |
| POST | `/api/admin/sweep` | Force a retention sweep now |

```bash
curl http://<robot>:8000/api/admin/disk
# {"used_bytes": 4194304000, "used_mb": 4194.30, "cap_mb": 2048, "cap_enabled": true, "session_count": 47}

curl -X POST http://<robot>:8000/api/admin/sweep
# {"deleted_ids": ["..."], "bytes_freed": 1073741824, "bytes_after": 3120562176, "cap_bytes": 2147483648}
```

### System

| Method | Path | Summary |
|---|---|---|
| GET | `/healthz` | Liveness |

---

## Integration patterns

### Prometheus Alertmanager

Route an alert to the agent's save endpoint via a webhook receiver:

```yaml
# alertmanager.yml
receivers:
  - name: missiondebug
    webhook_configs:
      - url: http://localhost:7000/sessions/save
        send_resolved: false
```

When an alert fires, Alertmanager POSTs a JSON body. The agent will save the last 60s and label it with whatever you pass in the body.

### Your own watchdog

```bash
#!/usr/bin/env bash
# Trigger MissionDebug if some external check fails.
if ! ros2 service call /critical_service ...; then
  curl -X POST http://localhost:7000/sessions/save \
    -H 'Content-Type: application/json' \
    -d "{\"label\":\"critical-service-down-$(date -Iseconds)\"}"
fi
```

### ros2_medkit Triggers

If you're running [ros2_medkit](https://github.com/selfpatch/ros2_medkit) on the same robot, its Trigger mechanism can fire a webhook on any fault condition. Point it at MissionDebug's save endpoint and you get medkit's live-ops diagnostics paired with MissionDebug's post-incident replay — no custom code on either side.

See [`docs/INTEGRATIONS.md`](./INTEGRATIONS.md) (forthcoming) for concrete configs.

---

## Generated OpenAPI

```bash
# Spec as JSON
curl http://<robot>:7000/openapi.json | jq .   # agent
curl http://<robot>:8000/openapi.json | jq .   # backend

# Interactive Swagger UI in your browser
open http://<robot>:7000/docs                  # agent
open http://<robot>:8000/docs                  # backend
```

Use the JSON spec to generate client libraries with `openapi-generator-cli`, `quicktype`, or your stack's preferred tool.
