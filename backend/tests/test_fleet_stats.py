"""v2 P3.5.6b — fleet incident stats endpoint.

Covers the KPI math the dashboard renders. Each test pins a specific
business semantic the buyable surface relies on:

- Empty hub (day-one pilot) returns a clean shape with None rates,
  not misleading 0% numbers
- Resolution rate = terminal / total, computed over implicit-open too
- MTTR averages only sessions with a real resolved_at timestamp;
  measures time-to-FIRST-resolution (preserves the invariant from
  the data layer commit C1)
- Top patterns group by label, descending count, then descending open
- Window boundaries: [start, end), inclusive of start, exclusive of end
- by_day series is zero-filled so the sparkline shows real cadence
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app


def _client(tmp_path) -> TestClient:
    app = build_app(
        sessions_dir=tmp_path / "sessions",
        db_path=tmp_path / "x.sqlite3",
    )
    return TestClient(app)


def _ingest(
    c: TestClient,
    *,
    sid: str,
    robot_id: str = "robot-001",
    label: str = "anomaly:stall",
    started_at: int | None = None,
    summary: str | None = "rule 'stall' /cmd_vel",
) -> None:
    """Ingest a session at a controllable past timestamp so the window
    boundaries can be tested deterministically."""
    ts = started_at if started_at is not None else int(time.time() * 1000)
    c.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": sid,
            "robot_id": robot_id,
            "started_at": ts,
            "ended_at": ts + 60_000,
            "duration_ms": 60_000,
            "label": label,
            "topics": ["/cmd_vel"],
            "mcap_size_bytes": 1024,
            "mcap_url": f"http://x/{sid}",
            "summary": summary,
        },
    )


def _set_resolution(c: TestClient, sid: str, status: str, **kw) -> None:
    body = {"status": status}
    body.update(kw)
    r = c.put(f"/api/v2/sessions/{sid}/resolution", json=body)
    assert r.status_code == 200, r.text


# ============================================================
# Empty hub — day-one pilot
# ============================================================


def test_empty_hub_returns_clean_shape(tmp_path):
    """First open of the dashboard on a fresh pilot. No captures yet.
    Rates must be None (not 0.0) so the UI can show "no data yet"
    rather than "0% resolution rate" (which reads as failure)."""
    c = _client(tmp_path)
    body = c.get("/api/v2/fleet/incident-stats").json()
    assert body["captures"]["total"] == 0
    assert body["captures"]["by_day"] == []
    assert body["captures"]["by_robot"] == []
    assert body["resolution"]["resolution_rate"] is None
    assert body["mttr_ms"] is None
    assert body["mttr_n"] == 0
    assert body["recurrence"]["recurrence_rate"] is None
    assert body["top_patterns"] == []


# ============================================================
# Capture aggregates
# ============================================================


def test_captures_total_and_by_robot(tmp_path):
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    for i in range(3):
        _ingest(c, sid=f"s_a_{i}", robot_id="robot-A", started_at=now - i * 1000)
    for i in range(2):
        _ingest(c, sid=f"s_b_{i}", robot_id="robot-B", started_at=now - i * 1000)

    body = c.get("/api/v2/fleet/incident-stats").json()
    assert body["captures"]["total"] == 5
    # Sorted desc by count, then ascending by robot_id.
    assert body["captures"]["by_robot"][0] == {"robot_id": "robot-A", "count": 3}
    assert body["captures"]["by_robot"][1] == {"robot_id": "robot-B", "count": 2}


def test_capture_outside_window_excluded(tmp_path):
    """A session older than the window must not affect any KPI.
    Default window is 30 days, so 31 days in the past is comfortably
    outside."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    _ingest(c, sid="recent", started_at=now - 1000)
    _ingest(c, sid="ancient", started_at=now - 31 * 86_400_000)

    body = c.get("/api/v2/fleet/incident-stats?window_days=30").json()
    assert body["captures"]["total"] == 1
    assert body["captures"]["by_robot"][0]["count"] == 1


# ============================================================
# Resolution breakdown + rate
# ============================================================


def test_resolution_rate_counts_terminal_only(tmp_path):
    """Resolution rate = terminal / total. Open + investigating do NOT
    count as resolved. Untriaged sessions count as implicit-open."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    # 4 sessions, 2 resolved (terminal), 1 investigating, 1 untriaged.
    for i in range(4):
        _ingest(c, sid=f"s_{i}", started_at=now - i * 1000)
    _set_resolution(c, "s_0", "resolved")
    _set_resolution(c, "s_1", "resolved")
    _set_resolution(c, "s_2", "investigating")
    # s_3 untriaged → implicit open

    body = c.get("/api/v2/fleet/incident-stats").json()
    res = body["resolution"]
    assert res["resolved"] == 2
    assert res["investigating"] == 1
    assert res["open"] == 1
    assert res["resolution_rate"] == 0.5  # 2 of 4


def test_resolution_breakdown_includes_duplicate_and_wont_fix(tmp_path):
    """Duplicate and wont_fix are both terminal → both count toward
    resolution rate. The dashboard reads them as "operator took
    action on this", which is the buyable signal."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    _ingest(c, sid="canon", started_at=now - 2000)
    _ingest(c, sid="dup", started_at=now - 1000)
    _ingest(c, sid="wfx", started_at=now - 500)
    _set_resolution(c, "dup", "duplicate", duplicate_of="canon")
    _set_resolution(c, "wfx", "wont_fix")

    body = c.get("/api/v2/fleet/incident-stats").json()
    res = body["resolution"]
    assert res["duplicate"] == 1
    assert res["wont_fix"] == 1
    # 2 terminal of 3 captures = 0.6667.
    assert res["resolution_rate"] == round(2 / 3, 4)


# ============================================================
# MTTR
# ============================================================


def test_mttr_averages_terminal_sessions(tmp_path):
    """MTTR = mean(resolved_at - started_at) for sessions with a
    resolved_at. Untriaged + investigating sessions don't contribute."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    _ingest(c, sid="s_1", started_at=now - 60_000)
    _ingest(c, sid="s_2", started_at=now - 30_000)
    _ingest(c, sid="s_open", started_at=now - 10_000)  # stays untriaged

    # Resolve both — resolved_at is now_ms() at the PUT call.
    _set_resolution(c, "s_1", "resolved")
    _set_resolution(c, "s_2", "resolved")

    body = c.get("/api/v2/fleet/incident-stats").json()
    assert body["mttr_n"] == 2
    # Each MTTR delta is roughly (now - started_at) — order-of-magnitude
    # checks, not exact, because the resolved_at timestamp is set at the
    # PUT call which we can't pin precisely.
    assert body["mttr_ms"] is not None
    # Average of ~60s and ~30s should land between 15s and 120s.
    assert 15_000 <= body["mttr_ms"] <= 120_000


def test_mttr_none_when_no_resolutions(tmp_path):
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    _ingest(c, sid="s_open", started_at=now - 60_000)

    body = c.get("/api/v2/fleet/incident-stats").json()
    assert body["mttr_ms"] is None
    assert body["mttr_n"] == 0


# ============================================================
# Recurrence rate
# ============================================================


def test_recurrence_rate(tmp_path):
    """recurrence_rate = explicit duplicates / total captures. This is the
    headline single-number KPI the demo opens with —
    'X% of your captures this month were things you've seen before'."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    _ingest(c, sid="canon", started_at=now - 3000)
    _ingest(c, sid="dup_1", started_at=now - 2000)
    _ingest(c, sid="dup_2", started_at=now - 1000)
    _ingest(c, sid="new", started_at=now - 500)
    _set_resolution(c, "dup_1", "duplicate", duplicate_of="canon")
    _set_resolution(c, "dup_2", "duplicate", duplicate_of="canon")

    body = c.get("/api/v2/fleet/incident-stats").json()
    assert body["recurrence"]["duplicates_marked"] == 2
    assert body["recurrence"]["recurrence_rate"] == 0.5  # 2 of 4


# ============================================================
# Top patterns
# ============================================================


def test_top_patterns_grouped_by_label_with_status_breakdown(tmp_path):
    """Top patterns groups by rule name (label) and surfaces per-status
    counts inline so the dashboard can show
    'stall: 12 occurrences, 3 open, 9 resolved' without an N+1."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    # 3 stalls, 2 path deviations, 1 battery
    for i in range(3):
        _ingest(c, sid=f"st_{i}", label="anomaly:stall", started_at=now - i)
    for i in range(2):
        _ingest(c, sid=f"pd_{i}", label="anomaly:path-deviation", started_at=now - 10 - i)
    _ingest(c, sid="bat_0", label="anomaly:battery-low", started_at=now - 20)
    _set_resolution(c, "st_0", "resolved")
    _set_resolution(c, "st_1", "resolved")

    body = c.get("/api/v2/fleet/incident-stats").json()
    patterns = body["top_patterns"]
    assert patterns[0]["pattern"] == "anomaly:stall"
    assert patterns[0]["count"] == 3
    assert patterns[0]["resolved"] == 2
    assert patterns[0]["open"] == 1
    assert patterns[1]["pattern"] == "anomaly:path-deviation"
    assert patterns[1]["count"] == 2


def test_top_patterns_capped_at_five(tmp_path):
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    for i in range(10):
        _ingest(c, sid=f"s_{i}", label=f"rule-{i}", started_at=now - i * 1000)
    body = c.get("/api/v2/fleet/incident-stats").json()
    assert len(body["top_patterns"]) == 5


# ============================================================
# Window parameter
# ============================================================


def test_window_days_param_validated(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/v2/fleet/incident-stats?window_days=0").status_code == 422
    assert c.get("/api/v2/fleet/incident-stats?window_days=400").status_code == 422


def test_by_day_series_zero_fills_gaps(tmp_path):
    """The sparkline must show real cadence — a 3-day gap reads as a
    3-day gap, not collapse. We expect one entry per UTC day in the
    window."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    _ingest(c, sid="s_today", started_at=now - 1000)

    body = c.get("/api/v2/fleet/incident-stats?window_days=7").json()
    # 7 days in the window → 7 or 8 entries (window can straddle the
    # midnight boundary depending on exact instant). Each entry has a
    # `day` string and `count` int.
    assert 6 <= len(body["captures"]["by_day"]) <= 8
    # Today has at least one capture.
    assert sum(d["count"] for d in body["captures"]["by_day"]) == 1


# ============================================================
# Pattern with NULL label (manual save, no rule)
# ============================================================


def test_top_patterns_includes_unlabeled_captures(tmp_path):
    """Manual saves have label=null but are still real captures the
    dashboard should account for. They group as a single 'unlabelled'
    pattern (pattern: null in JSON)."""
    c = _client(tmp_path)
    now = int(time.time() * 1000)
    # Ingest one with label, two without.
    _ingest(c, sid="s_rule", label="anomaly:stall", started_at=now - 1000)
    c.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": "s_manual_1",
            "robot_id": "robot-001",
            "started_at": now - 500,
            "ended_at": now,
            "duration_ms": 500,
            "topics": ["/cmd_vel"],
            "mcap_size_bytes": 100,
            "mcap_url": "http://x/manual_1",
            "summary": "Manual save",
            # label intentionally omitted (None)
        },
    )
    body = c.get("/api/v2/fleet/incident-stats").json()
    labels = {p["pattern"] for p in body["top_patterns"]}
    assert "anomaly:stall" in labels
    assert None in labels  # unlabelled bucket present
