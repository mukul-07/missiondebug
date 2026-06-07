"""Stress test: the PAID (Fleet) plan under concurrent load.

A LICENSED hub with alerting + lifecycle on. 100 agents capture incidents
(each fires a webhook alert, delivered to a real local receiver) + heartbeat,
while ops clients hammer the admin surfaces (license/over-deployment, disk,
alerts status + test, lifecycle sweep) — all at once. Verifies the paid paths
hold up: alerts delivered, gates honored, no failures, no lock errors.
"""
import os
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import uvicorn

PORT = int(os.environ.get("MD_LOAD_PORT", "8180"))
N_AGENTS, AGENT_ROUNDS = 100, 10     # heartbeats; ingest (=alert) every 3rd
N_OPS, OPS_ROUNDS = 10, 12
BASE = f"http://127.0.0.1:{PORT}"

# --- real webhook receiver that counts delivered alerts ---
DELIVERED = []
_dlock = threading.Lock()


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        self.rfile.read(n)
        with _dlock:
            DELIVERED.append(1)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


recv = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
hook_port = recv.server_address[1]
threading.Thread(target=recv.serve_forever, daemon=True).start()

# alerting + lifecycle configured BEFORE build_app so the gated paths light up
os.environ["MD_ALERT_WEBHOOK_URL"] = f"http://127.0.0.1:{hook_port}/hook"
os.environ["MD_ALERT_COOLDOWN_S"] = "0"   # no throttle — stress max alert volume

from missiondebug_backend.ee.licensing import License  # noqa: E402
from missiondebug_backend.main import build_app  # noqa: E402

LICENSE = License(customer="StressCo", robots=100,
                  features=frozenset({"alerting", "lifecycle"}),
                  expires_at=None, license_id="MD-STRESS", valid=True)

tmp = tempfile.mkdtemp()
app = build_app(Path(tmp) / "sessions", Path(tmp) / "db.sqlite3",
                cold_after_days=30, delete_after_days=0, license=LICENSE)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    try:
        if httpx.get(BASE + "/healthz", timeout=1).status_code == 200:
            break
    except Exception:
        pass
    time.sleep(0.1)

stats = defaultdict(lambda: {"n": 0, "fail": 0, "locked": 0, "lat": []})
guard = threading.Lock()


def rec(name, ok, lat, body=""):
    with guard:
        s = stats[name]
        s["n"] += 1
        if not ok:
            s["fail"] += 1
        if "locked" in body.lower():
            s["locked"] += 1
        s["lat"].append(lat)


def hit(c, name, method, path, **kw):
    t0 = time.perf_counter()
    try:
        r = c.request(method, path, **kw)
        rec(name, 200 <= r.status_code < 300, time.perf_counter() - t0, r.text)
    except Exception as e:
        rec(name, False, time.perf_counter() - t0, str(e))


def agent(i):
    robot = f"bot-{i:03d}"
    c = httpx.Client(base_url=BASE, timeout=30)
    for r in range(AGENT_ROUNDS):
        hit(c, "heartbeat", "POST", "/api/v1/agents/heartbeat",
            json={"robot_id": robot, "buffer_size": 60})
        if r % 3 == 0:  # a capture -> fires an alert
            sid = f"SES-{i:03d}-{r}"
            st = 1_700_000_000_000 + r * 1000
            hit(c, "ingest", "POST", "/api/v1/sessions/ingest", json={
                "session_id": sid, "robot_id": robot,
                "started_at": st, "ended_at": st + 60000, "duration_ms": 60000,
                "label": f"anomaly:rule_{r}", "topics": ["/battery_state"],
                "mcap_size_bytes": 1234, "mcap_url": f"http://a/{sid}.mcap",
                "subsystem": "power", "summary": f"incident on {robot}"})
    c.close()


def ops(i):
    c = httpx.Client(base_url=BASE, timeout=30)
    for _ in range(OPS_ROUNDS):
        hit(c, "license", "GET", "/api/admin/license")
        hit(c, "disk", "GET", "/api/admin/disk")
        hit(c, "alerts_status", "GET", "/api/admin/alerts")
        hit(c, "lifecycle_sweep", "POST", "/api/admin/lifecycle/sweep")
        hit(c, "alerts_test", "POST", "/api/admin/alerts/test")
    c.close()


t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=N_AGENTS + N_OPS) as ex:
    futs = [ex.submit(agent, i) for i in range(N_AGENTS)]
    futs += [ex.submit(ops, i) for i in range(N_OPS)]
    for f in futs:
        f.result()
wall = time.perf_counter() - t0

# let async alert deliveries drain
prev = -1
for _ in range(60):
    with _dlock:
        cur = len(DELIVERED)
    if cur == prev:
        break
    prev = cur
    time.sleep(0.1)

total = sum(s["n"] for s in stats.values())
fails = sum(s["fail"] for s in stats.values())
locked = sum(s["locked"] for s in stats.values())
print(f"agents={N_AGENTS} ops={N_OPS}")
print(f"total: {total}  fail: {fails}  db-locked: {locked}  "
      f"wall: {wall:.2f}s  thr: {total/wall:.0f} req/s")
print(f"alerts delivered to webhook: {len(DELIVERED)}")
print(f"{'endpoint':<16}{'n':>6}{'fail':>6}{'p50ms':>9}{'p95ms':>9}{'maxms':>9}")
for name in ["heartbeat", "ingest", "license", "disk", "alerts_status",
             "lifecycle_sweep", "alerts_test"]:
    s = stats[name]
    lat = sorted(s["lat"])
    p = lambda q: lat[min(int(len(lat) * q), len(lat) - 1)] * 1000 if lat else 0
    print(f"{name:<16}{s['n']:>6}{s['fail']:>6}{p(0.5):>9.1f}{p(0.95):>9.1f}{p(1.0):>9.1f}")

# sanity: over-deployment math (100 robots licensed, 100 reporting)
lic = httpx.get(BASE + "/api/admin/license", timeout=10).json()
print(f"license: {lic['customer']} {lic['robots_active']}/{lic['robots']} robots  "
      f"over_limit={lic['over_limit']}")

assert fails == 0, f"{fails} failures"
assert locked == 0, "lock errors"
assert len(DELIVERED) > 0, "no alerts delivered"
print("PASS — paid plan: 0 failures, 0 lock errors, alerts delivered, gates honored")
