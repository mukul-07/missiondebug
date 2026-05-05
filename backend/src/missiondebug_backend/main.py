from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Db
from .routes.files import get_router as files_router
from .routes.sessions import get_router as sessions_router
from .scanner import scan_directory

log = logging.getLogger(__name__)


def build_app(sessions_dir: Path, db_path: Path) -> FastAPI:
    db = Db(db_path)

    app = FastAPI(title="MissionDebug Backend")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Content-Length", "Accept-Ranges"],
    )

    def get_db() -> Db:
        return db

    app.include_router(sessions_router(get_db))
    app.include_router(files_router(get_db))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/api/admin/rescan")
    def rescan():
        n = scan_directory(sessions_dir, db)
        return {"indexed": n}

    @app.on_event("startup")
    def _startup():
        scan_directory(sessions_dir, db)

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                       format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions-dir",
        default=os.environ.get("MD_SESSIONS_DIR", "../agent/sessions"),
    )
    parser.add_argument("--db", default=os.environ.get("MD_DB", "./missiondebug.sqlite3"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app = build_app(Path(args.sessions_dir), Path(args.db))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
