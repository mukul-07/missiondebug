# MissionDebug Enterprise Edition — Commercial License, NOT MIT.
# Part of the paid Fleet/Enterprise tiers. Source is visible for evaluation and
# audit; commercial/production use requires a paid license + key. See ee/LICENSE
# and LICENSING.md. Copyright (c) 2026 MissionDebug. All rights reserved.
"""Lifecycle policies (v2 Phase 5b).

Age-based recording lifecycle, complementing the size-based disk cap in
``retention.py``:

  * ``cold_after_days`` — when a session is older than this, release its
    MCAP bytes (unlink the local file, clear the byte pointers) but KEEP
    the row. The session still appears in the dashboard / similarity /
    resolution; the replay UI shows the calm "recording unavailable"
    card. This is the pitch made concrete: incident memory outlives the
    recording.
  * ``delete_after_days`` — when a session is older than this, purge it
    entirely (row + local file). For fleets that must drop data on a
    schedule (retention compliance).

Both are opt-in (0 = disabled). When both are set, ``delete_after_days``
should be the larger window — a session crosses cold first, then delete.
The sweeper processes deletes before colds so a session past both
thresholds is purged outright rather than cooled then immediately purged.

Hard rules respected:
  * Retention/lifecycle is a backend concern — the single deleter
    (Hard Rule "retention is a backend concern"). The agent never runs this.
  * Hub is read-only on robots (HR22): cooling a hub-ingested session only
    clears the hub's fetch URL; it never deletes bytes on the robot. The
    robot's own copy is the robot's business.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from ..db import Db, now_ms

log = logging.getLogger(__name__)

_DAY_MS = 86_400_000


@dataclass
class LifecycleResult:
    cooled_ids: list[str]
    deleted_ids: list[str]
    cold_after_days: int
    delete_after_days: int


def _unlink_local(path: str | None) -> None:
    """Best-effort unlink of a local MCAP file. Remote-only sessions
    (mcap_path empty, bytes on the robot or in S3) have nothing local to
    remove — and the hub must not reach onto the robot (HR22)."""
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        log.exception("Could not unlink %s during lifecycle sweep", path)


def sweep_lifecycle_once(
    db: Db,
    *,
    cold_after_days: int,
    delete_after_days: int,
    now_ms_val: int | None = None,
    batch: int = 256,
) -> LifecycleResult:
    """Apply the configured age policies once. No-op for any policy set to
    <= 0. ``now_ms_val`` is injectable so tests don't depend on wall time."""
    now = now_ms_val if now_ms_val is not None else now_ms()
    deleted: list[str] = []
    cooled: list[str] = []

    # Purge first: a session past the delete threshold should be removed,
    # not cooled-then-removed.
    if delete_after_days > 0:
        cutoff = now - delete_after_days * _DAY_MS
        while True:
            rows = db.list_delete_candidates(cutoff_ms=cutoff, limit=batch)
            if not rows:
                break
            for row in rows:
                _unlink_local(row.mcap_path)
                db.delete_session(row.id)
                deleted.append(row.id)
            if len(rows) < batch:
                break

    # Then cool: release bytes for sessions past the cold threshold that
    # still hold a recording (and weren't just deleted above).
    if cold_after_days > 0:
        cutoff = now - cold_after_days * _DAY_MS
        while True:
            rows = db.list_cold_candidates(cutoff_ms=cutoff, limit=batch)
            if not rows:
                break
            for row in rows:
                _unlink_local(row.mcap_path)
                db.mark_cold(row.id, cold_at=now)
                cooled.append(row.id)
            if len(rows) < batch:
                break

    return LifecycleResult(
        cooled_ids=cooled,
        deleted_ids=deleted,
        cold_after_days=cold_after_days,
        delete_after_days=delete_after_days,
    )


async def run_periodic(
    db: Db,
    *,
    cold_after_days: int,
    delete_after_days: int,
    interval_s: float,
    stop: asyncio.Event,
) -> None:
    """Sleep, sweep, repeat. Cancel-safe via `stop`. Age-based, so a long
    interval (hourly) is plenty — nothing here is latency-sensitive."""
    while not stop.is_set():
        try:
            # asyncio.TimeoutError, NOT the builtin — distinct on Python 3.10
            # (see the periodic_rescan note in main.py).
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            return
        except asyncio.TimeoutError:
            pass
        try:
            result = await asyncio.to_thread(
                sweep_lifecycle_once,
                db,
                cold_after_days=cold_after_days,
                delete_after_days=delete_after_days,
            )
            if result.cooled_ids or result.deleted_ids:
                log.info(
                    "Lifecycle swept: cooled %d, deleted %d session(s)",
                    len(result.cooled_ids),
                    len(result.deleted_ids),
                )
        except Exception:
            log.exception("Lifecycle sweep failed")
