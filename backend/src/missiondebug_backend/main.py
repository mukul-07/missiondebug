from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Db
from .retention import run_periodic as run_retention, sweep_once
from .routes.annotations import get_router as annotations_router
from .routes.files import get_router as files_router
from .routes.sessions import get_router as sessions_router
from .scanner import scan_directory

log = logging.getLogger(__name__)

# How often to rescan the sessions directory for new MCAP files. Cheap because
# the scanner skips paths already in the DB.
RESCAN_INTERVAL_S = 5.0

# How often to check disk usage. Sweeps are cheap when under the cap
# (one SUM query) so frequent ticks are fine.
RETENTION_INTERVAL_S = 30.0


def build_app(
    sessions_dir: Path,
    db_path: Path,
    fixtures_dir: Path | None = None,
    max_disk_mb: int = 0,
) -> FastAPI:
    db = Db(db_path)

    scan_dirs: list[Path] = [sessions_dir]
    if fixtures_dir is not None and fixtures_dir.exists():
        scan_dirs.append(fixtures_dir)

    def scan_all() -> int:
        total = 0
        for d in scan_dirs:
            try:
                total += scan_directory(d, db)
            except Exception:
                log.exception("Scan failed for %s", d)
        return total

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Initial scan synchronously so /api/sessions is populated before
        # the first request lands.
        scan_all()

        stop = asyncio.Event()

        async def periodic_rescan():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=RESCAN_INTERVAL_S)
                    return  # stop fired — exit
                except asyncio.TimeoutError:
                    pass
                try:
                    n = await asyncio.to_thread(scan_all)
                    if n:
                        log.info("Rescan picked up %d new session(s)", n)
                except Exception:
                    log.exception("Periodic rescan failed")

        cap_bytes = max_disk_mb * 1024 * 1024

        rescan_task = asyncio.create_task(periodic_rescan(), name="periodic_rescan")
        retention_task: asyncio.Task | None = None
        if cap_bytes > 0:
            retention_task = asyncio.create_task(
                run_retention(db, cap_bytes, RETENTION_INTERVAL_S, stop),
                name="periodic_retention",
            )
            log.info("Disk retention enabled: cap %d MB", max_disk_mb)
        try:
            yield
        finally:
            stop.set()
            for t in (rescan_task, retention_task):
                if t is None:
                    continue
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(title="MissionDebug Backend", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Content-Length", "Accept-Ranges"],
    )

    def get_db() -> Db:
        return db

    app.include_router(sessions_router(get_db))
    app.include_router(files_router(get_db))
    app.include_router(annotations_router(get_db))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/api/admin/rescan")
    def rescan():
        n = scan_directory(sessions_dir, db)
        return {"indexed": n}

    @app.get("/api/admin/disk")
    def disk_usage():
        cap_bytes = max_disk_mb * 1024 * 1024
        used = db.total_mcap_bytes()
        return {
            "used_bytes": used,
            "used_mb": round(used / 1e6, 2),
            "cap_mb": max_disk_mb,
            "cap_enabled": cap_bytes > 0,
            "session_count": len(db.list_sessions(limit=10**9)),
        }

    @app.post("/api/admin/sweep")
    def sweep():
        cap_bytes = max_disk_mb * 1024 * 1024
        result = sweep_once(db, cap_bytes)
        return {
            "deleted_ids": result.deleted_ids,
            "bytes_freed": result.bytes_freed,
            "bytes_after": result.bytes_after,
            "cap_bytes": result.cap_bytes,
        }

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions-dir",
        default=os.environ.get("MD_SESSIONS_DIR", "../agent/sessions"),
    )
    parser.add_argument(
        "--fixtures-dir",
        default=os.environ.get("MD_FIXTURES_DIR", "../fixtures"),
        help="Directory of demo MCAP files to also index (set MD_FIXTURES=1 to enable)",
    )
    parser.add_argument("--db", default=os.environ.get("MD_DB", "./missiondebug.sqlite3"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--max-disk-mb",
        type=int,
        default=int(os.environ.get("MD_MAX_DISK_MB", "0")),
        help="Cap on total MCAP bytes; oldest sessions deleted when over. 0 = disabled.",
    )
    args = parser.parse_args()

    fixtures = (
        Path(args.fixtures_dir) if os.environ.get("MD_FIXTURES") == "1" else None
    )
    app = build_app(
        Path(args.sessions_dir),
        Path(args.db),
        fixtures_dir=fixtures,
        max_disk_mb=args.max_disk_mb,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
