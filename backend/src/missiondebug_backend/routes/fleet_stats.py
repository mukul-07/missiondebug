"""v2 P3.5.6b — fleet incident stats endpoint.

The buyable surface. A single endpoint that rolls up every session
captured in a configurable time window into the four business-facing
KPIs that determine whether a 100-robot fleet customer signs the
check:

  - Captures: how many incidents this window, sparkline trend, per-robot
  - Resolution: counts per status + resolution rate
  - MTTR: mean time-to-first-resolution
  - Recurrence: how many of the new captures are duplicates of past ones
  - Top patterns: the failure modes burning the most ops time

All computed in a single Python pass over the
`list_sessions_in_window` denormalised join — no N+1 queries, no
precomputed materialised views (those are a v2.x problem when a real
fleet crosses 10K sessions per window).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ..db import Db, FleetSessionRow, TERMINAL_STATUSES


_MS_PER_DAY = 24 * 60 * 60 * 1000


def _start_of_day_utc_ms(ts_ms: int) -> int:
    """Snap a unix-ms timestamp to the start of its UTC day. Used for
    binning the captures trend so the sparkline is deterministic across
    timezones."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    floor = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(floor.timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _empty_response(*, window_days: int, start: int, end: int) -> dict:
    """The shape the dashboard renders on day-one — fresh hub, no
    captures yet. Numeric KPIs are 0 / None so the UI can show
    "no data yet" rather than misleading zeros."""
    return {
        "window_days": window_days,
        "window_start_ms": start,
        "window_end_ms": end,
        "captures": {"total": 0, "by_day": [], "by_robot": []},
        "resolution": {
            "open": 0,
            "investigating": 0,
            "resolved": 0,
            "duplicate": 0,
            "wont_fix": 0,
            "resolution_rate": None,
        },
        "mttr_ms": None,
        "mttr_n": 0,
        "recurrence": {
            "duplicates_marked": 0,
            "recurrence_rate": None,
        },
        "top_patterns": [],
    }


def _by_day_series(rows: list[FleetSessionRow], start_ms: int, end_ms: int) -> list[dict]:
    """Per-UTC-day capture counts, with zero-filled gaps so the sparkline
    shows the actual cadence (a 3-day gap should read as a 3-day gap,
    not collapse). One row per day in the window, ordered ascending."""
    counts: Counter[int] = Counter()
    for r in rows:
        counts[_start_of_day_utc_ms(r.started_at)] += 1

    series = []
    day = _start_of_day_utc_ms(start_ms)
    final = _start_of_day_utc_ms(end_ms)
    while day <= final:
        series.append({
            "day": datetime.fromtimestamp(day / 1000, tz=timezone.utc)
                .strftime("%Y-%m-%d"),
            "count": counts.get(day, 0),
        })
        day += _MS_PER_DAY
    return series


def _by_robot(rows: list[FleetSessionRow]) -> list[dict]:
    """Robots ranked by capture volume, descending. Ties broken by
    robot_id ascending so identical fleets always produce identical
    rankings."""
    counts: Counter[str] = Counter(r.robot_id for r in rows)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"robot_id": rid, "count": n} for rid, n in ranked]


def _resolution_breakdown(rows: list[FleetSessionRow]) -> dict:
    """Counts per status (including implicit-open) plus the resolution
    rate. Rate is None when there are no captures — the UI then renders
    "no data" instead of "0%" which would be misleading."""
    counts = Counter(r.effective_status for r in rows)
    total = sum(counts.values())
    terminal = sum(counts[s] for s in TERMINAL_STATUSES)
    rate = None if total == 0 else round(terminal / total, 4)
    return {
        "open": counts.get("open", 0),
        "investigating": counts.get("investigating", 0),
        "resolved": counts.get("resolved", 0),
        "duplicate": counts.get("duplicate", 0),
        "wont_fix": counts.get("wont_fix", 0),
        "resolution_rate": rate,
    }


def _mttr(rows: list[FleetSessionRow]) -> tuple[int | None, int]:
    """Mean time-to-first-resolution in milliseconds, and the sample size
    that contributed. We define MTTR over sessions that
      (a) started in the window AND
      (b) have a resolved_at timestamp (any terminal status counts —
          resolved, duplicate, wont_fix all represent operator action).

    Returns (None, 0) when no sessions qualify — UI shows "no data".
    """
    deltas = [
        r.res_resolved_at - r.started_at
        for r in rows
        if r.res_resolved_at is not None and r.res_resolved_at >= r.started_at
    ]
    if not deltas:
        return None, 0
    return int(sum(deltas) / len(deltas)), len(deltas)


def _recurrence(rows: list[FleetSessionRow]) -> dict:
    """Duplicates-marked is the count of sessions explicitly labelled
    duplicate in this window. Rate is duplicates / total captures —
    the buyable single-number "X% of your captures this month were
    things you've seen before."

    Note: this counts EXPLICIT duplicates (operator-marked) only. The
    similarity-driven implicit-duplicate count is more aggressive and
    lands in P3.5.7+ when we have signal that operators actually want it.
    """
    duplicates = sum(1 for r in rows if r.effective_status == "duplicate")
    total = len(rows)
    rate = None if total == 0 else round(duplicates / total, 4)
    return {"duplicates_marked": duplicates, "recurrence_rate": rate}


def _top_patterns(rows: list[FleetSessionRow], *, k: int = 5) -> list[dict]:
    """Top recurring failure modes by capture count. v1 groups by `label`
    (rule name) — the natural unit a fleet operator thinks in. Each
    pattern surfaces its open/resolved breakdown so the dashboard can
    show "stall: 12 occurrences, 3 open, 9 resolved" without N+1.

    Untriaged captures (open) are the ones that need attention; the
    ranking surfaces them first when counts tie."""
    by_label: dict[str | None, dict] = {}
    for r in rows:
        key = r.label
        entry = by_label.setdefault(key, {
            "pattern": key,
            "count": 0,
            "open": 0,
            "investigating": 0,
            "resolved": 0,
            "duplicate": 0,
            "wont_fix": 0,
        })
        entry["count"] += 1
        entry[r.effective_status] += 1

    ranked = sorted(
        by_label.values(),
        key=lambda e: (-e["count"], -e["open"], str(e["pattern"] or "")),
    )
    return ranked[:k]


def get_router(get_db) -> APIRouter:
    router = APIRouter(prefix="/api/v2/fleet", tags=["fleet"])

    @router.get(
        "/incident-stats",
        summary="Fleet incident KPIs (captures, MTTR, resolution rate, recurrence, top patterns)",
    )
    def incident_stats(
        window_days: int = Query(
            30,
            ge=1,
            le=365,
            description=(
                "Lookback window in days, ending at the current UTC instant. "
                "Default 30. The dashboard's primary card; longer windows "
                "smooth trends, shorter ones highlight recent regressions."
            ),
        ),
        db: Db = Depends(get_db),
    ) -> dict:
        """Single-query rollup over the sliding window. Computed on read —
        no materialised views. For pilot-scale fleets (<10K captures /
        window) this runs in well under 100ms; the precompute story is a
        v2.x problem.

        All KPIs share the same window boundaries: `window_end_ms` is
        the current UTC instant; `window_start_ms = end - window_days *
        86400000`. Captures binned by their `started_at`.
        """
        end_ms = _now_ms()
        start_ms = end_ms - window_days * _MS_PER_DAY

        rows = db.list_sessions_in_window(
            started_at_gte=start_ms,
            started_at_lt=end_ms,
        )

        if not rows:
            return _empty_response(
                window_days=window_days, start=start_ms, end=end_ms,
            )

        mttr_ms, mttr_n = _mttr(rows)

        return {
            "window_days": window_days,
            "window_start_ms": start_ms,
            "window_end_ms": end_ms,
            "captures": {
                "total": len(rows),
                "by_day": _by_day_series(rows, start_ms, end_ms),
                "by_robot": _by_robot(rows),
            },
            "resolution": _resolution_breakdown(rows),
            "mttr_ms": mttr_ms,
            "mttr_n": mttr_n,
            "recurrence": _recurrence(rows),
            "top_patterns": _top_patterns(rows),
        }

    return router
