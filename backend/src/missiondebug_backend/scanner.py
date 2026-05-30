"""Scan a sessions directory and ingest any new MCAP files into the DB."""

from __future__ import annotations

import logging
from pathlib import Path

from .db import Db, SessionRow, now_ms
from .mcap_meta import extract

log = logging.getLogger(__name__)


def scan_directory(sessions_dir: Path, db: Db, robot_id_default: str = "robot-001") -> int:
    """Returns the number of newly-indexed sessions."""
    if not sessions_dir.exists():
        log.warning("Sessions dir %s does not exist", sessions_dir)
        return 0

    known = db.known_paths()
    indexed = 0
    for mcap in sorted(sessions_dir.glob("*.mcap")):
        path_str = str(mcap.resolve())
        if path_str in known:
            continue

        stem = mcap.stem
        # A row may already exist for this session id from a hub ingest,
        # which stores summary/subsystem/mcap_url but leaves mcap_path
        # empty (so it isn't in known_paths above). Re-inserting a
        # scan-derived row would INSERT OR REPLACE over those richer
        # fields and null out the summary. Instead, just attach the local
        # file so the bytes become servable, and leave the hub data intact.
        existing = db.get_session(stem)
        if existing is not None:
            if not existing.mcap_path:
                db.attach_mcap_path(stem, path_str)
                log.info("Attached local MCAP to hub-ingested session %s", stem)
            continue

        try:
            meta = extract(mcap)
        except Exception:
            log.exception("Failed to extract metadata from %s", mcap)
            continue

        # Prefer robot_id from MCAP metadata; fall back to filename convention.
        if meta.robot_id:
            robot_id = meta.robot_id
        elif "_" in stem:
            robot_id = stem.split("_", 1)[0]
        else:
            robot_id = robot_id_default

        row = SessionRow(
            id=stem,
            robot_id=robot_id,
            started_at=meta.started_at_ms,
            ended_at=meta.ended_at_ms,
            duration_ms=meta.duration_ms,
            label=meta.label,
            mcap_path=path_str,
            mcap_size_bytes=meta.size_bytes,
            topics=meta.topics,
            created_at=now_ms(),
            subsystem=meta.subsystem,
        )
        db.upsert_session(row)
        indexed += 1
        log.info("Indexed session %s (%d topics, %d ms)", row.id, len(row.topics), row.duration_ms)

    return indexed
