"""Load test: ~100 concurrent agents pounding the hub (heartbeats + ingests).

Starts the REAL hub via uvicorn, then 100 agent threads hammer it at once.
Verifies the WAL + busy_timeout hardening holds at fleet scale — no failures,
no "database is locked", and all data lands.
"""
import threading
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import uvicorn

from missiondebug_backend.main import build_app

PORT = 8137
N_AGENTS = 100
ROUNDS = 20            # heartbeats per agent; ingest every 5th -> 4 incidents/agent
BASE = f"http://127.0.0.1:{PORT}"

tmp = tempfile.mkdtemp()
app = build_app(Path(tmp) / "sessions", Path(tmp) / "db.sqlite3")
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()

# wait until the server answers
for _ in range(100):
    try:
        if httpx.get(BASE + "/healthz", timeout=1).status_code == 200:
            break
    except Exception:
        pass
    time.sleep(0.1)
else:
    raise SystemExit("server did not start")

res = {"ok": 0, "fail": 0, "locked": 0, "lat": []}
guard = threading.Lock()


def record(ok, latency, body=""):
    with guard:
        res["ok" if ok else "fail"] += 1
        if "locked" in body.lower():
            res["locked"] += 1
        res["lat"].append(latency)


def agent(i):
    robot = f"bot-{i:03d}"
    c = httpx.Client(base_url=BASE, timeout=30)
    for r in range(ROUNDS):
        t0 = time.perf_counter()
        try:
            resp = c.post("/api/v1/agents/heartbeat", json={"robot_id": robot, "buffer_size": 60})
            record(200 <= resp.status_code < 300, time.perf_counter() - t0, resp.text)
        except Exception as e:
            record(False, time.perf_counter() - t0, str(e))
        if r % 5 == 0:  # an incident
            sid = f"SES-{i:03d}-{r}"
            started = 1_700_000_000_000 + r * 1000
            t0 = time.perf_counter()
            try:
                resp = c.post("/api/v1/sessions/ingest", json={
                    "session_id": sid, "robot_id": robot,
                    "started_at": started, "ended_at": started + 60000, "duration_ms": 60000,
                    "label": "anomaly:battery_low", "topics": ["/battery_state", "/cmd_vel"],
                    "mcap_size_bytes": 1234, "mcap_url": f"http://agent/{sid}.mcap",
                    "subsystem": "power", "summary": f"battery_low on {robot}",
                })
                record(200 <= resp.status_code < 300, time.perf_counter() - t0, resp.text)
            except Exception as e:
                record(False, time.perf_counter() - t0, str(e))
    c.close()


t_start = time.perf_counter()
with ThreadPoolExecutor(max_workers=N_AGENTS) as ex:
    list(ex.map(agent, range(N_AGENTS)))
wall = time.perf_counter() - t_start

lat = sorted(res["lat"])
total = res["ok"] + res["fail"]
p = lambda q: lat[min(int(len(lat) * q), len(lat) - 1)] * 1000 if lat else 0

print(f"agents={N_AGENTS}  rounds={ROUNDS}")
print(f"total requests : {total}   (heartbeats + ingests)")
print(f"  ok           : {res['ok']}")
print(f"  failed       : {res['fail']}")
print(f"  db-locked    : {res['locked']}")
print(f"wall time      : {wall:.2f}s   throughput: {total / wall:.0f} req/s")
print(f"latency        : p50 {p(0.5):.1f}ms   p95 {p(0.95):.1f}ms   max {p(1.0):.1f}ms")

# /api/sessions caps limit at 200 — paginate to count all ingested sessions.
all_sessions, robots = [], set()
for off in range(0, N_AGENTS * 4 + 200, 200):
    page = httpx.get(BASE + f"/api/sessions?limit=200&offset={off}", timeout=15).json()
    all_sessions += page["sessions"]
    robots |= set(page["robots"])
    if not page["sessions"]:
        break
print(f"data landed    : {len(all_sessions)} sessions / {len(robots)} robots")

assert res["fail"] == 0, f"{res['fail']} requests FAILED"
assert res["locked"] == 0, "database-is-locked errors occurred"
assert len(robots) == N_AGENTS, f"only {len(robots)}/{N_AGENTS} robots registered"
assert len(all_sessions) == N_AGENTS * 4, f"only {len(all_sessions)}/{N_AGENTS * 4} incidents ingested"
print("\nPASS — 100 concurrent agents, zero failures, zero lock errors, all data landed")
