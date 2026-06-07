"""Stress test: the FREE (Community) plan API surface under concurrent load.

Seeds an 800-session corpus, then 30 concurrent clients hammer every free
endpoint — session list/detail, the incident dashboard, similarity ("has this
happened before"), resolutions, annotations — and we report per-endpoint
latency so the compute-heavy ones (dashboard rollup, TF-IDF similarity) are
visible.
"""
import os
import threading
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import uvicorn

from missiondebug_backend.db import Db, SessionRow, now_ms
from missiondebug_backend.main import build_app

PORT = int(os.environ.get("MD_LOAD_PORT", "8150"))
N_SESSIONS, N_CLIENTS, ROUNDS = 800, 30, 6
BASE = f"http://127.0.0.1:{PORT}"
DAY = 86_400_000
ROBOTS = [f"bot-{i:02d}" for i in range(30)]
RULES = ["battery_low", "stall", "topic_dropout", "path_deviation", "motor_stall"]
SUBS = ["power", "navigation", "perception", "drive"]

tmp = tempfile.mkdtemp()
db_path = Path(tmp) / "db.sqlite3"
app = build_app(Path(tmp) / "sessions", db_path)

db = Db(db_path)
now = now_ms()
ids = []
for i in range(N_SESSIONS):
    rule, robot, sub = RULES[i % 5], ROBOTS[i % 30], SUBS[i % 4]
    sid = f"SES-{i:04d}"
    started = now - (i % 25) * DAY - i * 1000
    db.upsert_session(SessionRow(
        id=sid, robot_id=robot, started_at=started, ended_at=started + 60000,
        duration_ms=60000, label=f"anomaly:{rule}", mcap_path="", mcap_size_bytes=1234,
        topics=["/battery_state", "/cmd_vel", "/odom", "/scan"], created_at=started,
        subsystem=sub,
        summary=(f"Auto-triggered by rule '{rule}' on {robot} (subsystem: {sub}). "
                 f"Captured 60s across topics: /battery_state (300 msgs), "
                 f"/cmd_vel (180 msgs), /odom (600 msgs), /scan (290 msgs)."),
    ))
    ids.append(sid)
print(f"seeded {N_SESSIONS} sessions")

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


def client(ci):
    c = httpx.Client(base_url=BASE, timeout=30)
    for r in range(ROUNDS):
        sid = ids[(ci * ROUNDS + r) % N_SESSIONS]
        hit(c, "list", "GET", "/api/sessions?limit=200")
        hit(c, "detail", "GET", f"/api/sessions/{sid}")
        hit(c, "dashboard", "GET", "/api/v2/fleet/incident-stats?window_days=30")
        hit(c, "similarity", "GET", f"/api/v2/sessions/{sid}/similar?k=3")
        hit(c, "resolution_get", "GET", f"/api/v2/sessions/{sid}/resolution")
        hit(c, "resolution_put", "PUT", f"/api/v2/sessions/{sid}/resolution",
            json={"status": "investigating", "root_cause": "load test"})
        hit(c, "annotation_list", "GET", f"/api/sessions/{sid}/annotations")
        hit(c, "annotation_post", "POST", f"/api/sessions/{sid}/annotations",
            json={"time_ns": 1000 + r, "body": "load test note"})
    c.close()


t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=N_CLIENTS) as ex:
    list(ex.map(client, range(N_CLIENTS)))
wall = time.perf_counter() - t0

total = sum(s["n"] for s in stats.values())
fails = sum(s["fail"] for s in stats.values())
locked = sum(s["locked"] for s in stats.values())
print(f"clients={N_CLIENTS} rounds={ROUNDS} corpus={N_SESSIONS}")
print(f"total: {total}  fail: {fails}  db-locked: {locked}  wall: {wall:.2f}s  thr: {total/wall:.0f} req/s")
print(f"{'endpoint':<16}{'n':>6}{'fail':>6}{'p50ms':>9}{'p95ms':>9}{'maxms':>9}")
for name in ["list", "detail", "dashboard", "similarity", "resolution_get",
             "resolution_put", "annotation_list", "annotation_post"]:
    s = stats[name]
    lat = sorted(s["lat"])
    p = lambda q: lat[min(int(len(lat) * q), len(lat) - 1)] * 1000 if lat else 0
    print(f"{name:<16}{s['n']:>6}{s['fail']:>6}{p(0.5):>9.1f}{p(0.95):>9.1f}{p(1.0):>9.1f}")
assert fails == 0, f"{fails} failures"
assert locked == 0, "lock errors"
print("PASS — all free-plan endpoints, 0 failures, 0 lock errors")
