"""Concurrency regression: many agents writing at once must not hit
"database is locked". Guards the WAL + busy_timeout config in db.py — the
hub's fleet-scale ("100+ robots") claim depends on it. A full 100-agent
HTTP load test lives in scripts/load_test_hub.py; this is the fast,
deterministic guard that runs in CI.
"""

from __future__ import annotations

import threading

from missiondebug_backend.db import Db, SessionRow, now_ms


def test_concurrent_writes_no_lock_errors(tmp_path):
    db = Db(tmp_path / "db.sqlite3")
    errors: list[str] = []
    n_agents = 24

    def worker(i: int) -> None:
        try:
            for r in range(25):
                db.record_heartbeat(robot_id=f"bot-{i:02d}", buffer_size=r)
                if r % 5 == 0:  # an incident every 5th round -> 5 per agent
                    started = now_ms()
                    db.upsert_session(SessionRow(
                        id=f"S-{i:02d}-{r}", robot_id=f"bot-{i:02d}",
                        started_at=started, ended_at=started + 1000, duration_ms=1000,
                        label="anomaly:x", mcap_path="", mcap_size_bytes=1,
                        topics=["/t"], created_at=started,
                    ))
        except Exception as e:  # OperationalError("database is locked"), etc.
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_agents)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent writes errored: {errors[:3]}"
    assert len(db.list_robot_ids()) == n_agents               # every agent landed
    assert len(db.list_sessions(limit=10_000)) == n_agents * 5  # every incident landed
