"""Disk-retention sweeper (v1.5 Phase 4).

When the total bytes of indexed MCAPs exceeds a cap, delete the oldest
sessions (by started_at) until under the cap. Both the row and the
underlying file are removed; cascade-delete handles annotations.

Pure logic + a small wrapper to schedule it inside FastAPI's lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from .db import Db

log = logging.getLogger(__name__)


@dataclass
class RetentionResult:
    deleted_ids: list[str]
    bytes_freed: int
    bytes_after: int
    cap_bytes: int


def sweep_once(db: Db, cap_bytes: int) -> RetentionResult:
    """Delete oldest sessions until total mcap bytes <= cap_bytes.

    No-op if cap_bytes <= 0 (treated as "disabled") or already under.
    """
    if cap_bytes <= 0:
        return RetentionResult([], 0, db.total_mcap_bytes(), cap_bytes)

    total = db.total_mcap_bytes()
    if total <= cap_bytes:
        return RetentionResult([], 0, total, cap_bytes)

    deleted: list[str] = []
    bytes_freed = 0

    while total > cap_bytes:
        # Page through oldest in chunks; each delete updates the running
        # total without an extra SUM query.
        batch = db.list_oldest_sessions(limit=16)
        if not batch:
            break
        for row in batch:
            try:
                Path(row.mcap_path).unlink(missing_ok=True)
            except OSError:
                # Couldn't unlink (perms, mount gone). Drop the DB row
                # anyway — leaving it would re-pick the same session next
                # sweep and loop forever.
                log.exception("Could not unlink %s during retention", row.mcap_path)
            db.delete_session(row.id)
            deleted.append(row.id)
            bytes_freed += row.mcap_size_bytes
            total -= row.mcap_size_bytes
            if total <= cap_bytes:
                break

    return RetentionResult(deleted, bytes_freed, total, cap_bytes)


async def run_periodic(
    db: Db,
    cap_bytes: int,
    interval_s: float,
    stop: asyncio.Event,
) -> None:
    """Sleep, sweep, repeat. Cancel-safe via `stop`."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            return
        except asyncio.TimeoutError:
            pass
        try:
            result = await asyncio.to_thread(sweep_once, db, cap_bytes)
            if result.deleted_ids:
                log.info(
                    "Retention swept %d session(s), freed %.1f MB (now %.1f / %.1f MB)",
                    len(result.deleted_ids),
                    result.bytes_freed / 1e6,
                    result.bytes_after / 1e6,
                    cap_bytes / 1e6,
                )
        except Exception:
            log.exception("Retention sweep failed")
