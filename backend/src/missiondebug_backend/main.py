from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse
from starlette.types import Scope

from .auth import AuthConfig, _check_basic, _check_bearer


class SpaStaticFiles(StaticFiles):
    """StaticFiles + SPA-fallback.

    Default StaticFiles returns 404 for any path that doesn't map to a
    real file on disk. That breaks deep-linked routes the React app
    handles client-side (e.g. /sessions/<id>?t=10.0) — pasting a Copy-
    link URL into a fresh tab would hit FastAPI's 404 JSON instead of
    the SPA shell.

    Override: on 404, fall back to index.html unless the request was
    for a real-looking asset (has a file extension), in which case
    the 404 is correct. The SPA then reads location.pathname and
    routes accordingly.
    """

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as ex:
            if ex.status_code != 404:
                raise
            # API paths must return JSON 404s, not the SPA shell — masking
            # /api/* 404s would silently break clients (curl, agents, etc.).
            if path.startswith("api/") or path == "api":
                raise
            # Asset paths (with a file extension in the last segment) also
            # let the 404 stand — failing fast is better than silently
            # serving the SPA shell where a real .js/.css was expected.
            if "." in path.rsplit("/", 1)[-1]:
                raise
            # Otherwise it's a SPA deep-link (e.g. /sessions/<id>): serve
            # index.html and let React Router handle it client-side.
            index = Path(self.directory) / "index.html"
            return FileResponse(index)

from .alerting import AlertEvent, Alerter, build_alerter
from .db import Db
from .incident_agent import IncidentAgent, build_incident_agent
from .lifecycle import run_periodic as run_lifecycle
from .lifecycle import sweep_lifecycle_once
from .retention import run_periodic as run_retention
from .retention import sweep_once
from .routes.agents import get_router as agents_router
from .routes.annotations import get_router as annotations_router
from .routes.files import get_router as files_router
from .routes.fleet_stats import get_router as fleet_stats_router
from .routes.incidents import get_router as incidents_router
from .routes.ingest import get_router as ingest_router
from .routes.resolutions import get_router as resolutions_router
from .routes.sessions import get_router as sessions_router
from .routes.similarity import get_router as similarity_router
from .scanner import scan_directory
from .telemetry import Telemetry, build_telemetry

log = logging.getLogger(__name__)

# How often to rescan the sessions directory for new MCAP files. Cheap because
# the scanner skips paths already in the DB.
RESCAN_INTERVAL_S = 5.0

# How often to check disk usage. Sweeps are cheap when under the cap
# (one SUM query) so frequent ticks are fine.
RETENTION_INTERVAL_S = 30.0

# How often to apply age-based lifecycle policies (cold/delete). Age-based,
# so nothing here is latency-sensitive — hourly is plenty.
LIFECYCLE_INTERVAL_S = 3600.0


def _env_int(name: str, default: int = 0) -> int:
    """Read an int from the environment, treating unset OR empty/whitespace
    as the default. Compose passes `"${VAR:-}"` as an empty string (the key
    exists), so a bare `int(os.environ.get(name, "0"))` would choke on `""`
    and crash startup — this is the robust form."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("Ignoring non-integer %s=%r; using %d", name, raw, default)
        return default


def build_app(
    sessions_dir: Path,
    db_path: Path,
    fixtures_dir: Path | None = None,
    max_disk_mb: int = 0,
    cold_after_days: int = 0,
    delete_after_days: int = 0,
    web_dir: Path | None = None,
    auth_config: AuthConfig | None = None,
    telemetry: Telemetry | None = None,
    incident_agent: IncidentAgent | None = None,
    alerter: Alerter | None = None,
) -> FastAPI:
    db = Db(db_path)
    # v2 OTel — opt-in export to the operator's observability stack. No-op
    # unless MD_OTEL_ENDPOINT is set (and the [otel] extra installed), so
    # standalone / air-gapped installs are unaffected. Tests inject their own.
    telemetry = telemetry if telemetry is not None else build_telemetry(db)
    # v2 incident agent — natural-language Q&A over the incident history.
    # Opt-in: inert unless an LLM key (MD_LLM_API_KEY) is configured. Tests
    # inject one with a scripted model.
    incident_agent = incident_agent if incident_agent is not None else build_incident_agent(db)
    # v2 P6 alerting — webhook notifications on capture. No-op unless a
    # destination (Slack / PagerDuty / generic) is configured. Tests inject
    # one with a recording post_fn.
    alerter = alerter if alerter is not None else build_alerter()
    # Auth config — caller is expected to have already validated startup
    # invariants in main(). Defaulting here keeps existing test callers
    # (which pass no auth_config) seeing v1.5 open-routes behaviour.
    auth_cfg = auth_config or AuthConfig()

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
                # NOTE: asyncio.TimeoutError, NOT the builtin TimeoutError —
                # they are distinct classes on Python 3.10 (the project's
                # floor), only merged in 3.11. Catching the builtin here let
                # the timeout propagate and killed the rescan loop on 3.10.
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

        # v2 P5b — age-based lifecycle (cold-tier + delete). Opt-in; only
        # scheduled when at least one policy is configured.
        lifecycle_task: asyncio.Task | None = None
        if cold_after_days > 0 or delete_after_days > 0:
            lifecycle_task = asyncio.create_task(
                run_lifecycle(
                    db,
                    cold_after_days=cold_after_days,
                    delete_after_days=delete_after_days,
                    interval_s=LIFECYCLE_INTERVAL_S,
                    stop=stop,
                ),
                name="periodic_lifecycle",
            )
            log.info(
                "Lifecycle policies enabled: cold_after=%s delete_after=%s (days)",
                cold_after_days or "off",
                delete_after_days or "off",
            )
        try:
            yield
        finally:
            stop.set()
            for t in (rescan_task, retention_task, lifecycle_task):
                if t is None:
                    continue
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            # Flush + shut down OTel exporters (no-op when disabled).
            await asyncio.to_thread(telemetry.shutdown)

    app = FastAPI(
        title="MissionDebug Backend",
        description=(
            "Indexes captured MCAP sessions, serves them with HTTP Range "
            "for browser-side replay, and manages disk retention. The web "
            "UI is mounted at the root when `--web-dir` is set; the API "
            "lives under `/api/*`. See https://github.com/mukul-07/missiondebug "
            "for the architecture and the agent that produces the MCAP files."
        ),
        version="1.5.0",
        license_info={"name": "MIT", "url": "https://github.com/mukul-07/missiondebug/blob/main/LICENSE"},
        contact={"name": "MissionDebug", "url": "https://github.com/mukul-07/missiondebug"},
        openapi_tags=[
            {"name": "sessions", "description": "List, filter, and inspect captured sessions."},
            {"name": "files", "description": "Stream MCAP bytes with HTTP Range — used by the browser scrubber."},
            {"name": "annotations", "description": "Per-session timestamped notes."},
            {"name": "admin", "description": "Disk usage, manual rescan, retention sweep."},
            {"name": "system", "description": "Liveness."},
            {"name": "ingest", "description": "Agents post session metadata to the hub (v2 fleet)."},
            {"name": "agents", "description": "Agent heartbeats and fleet roster (v2 fleet)."},
        ],
        lifespan=lifespan,
    )
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

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        """v2 P4 — gate every /api/* request on the configured credentials.

        Static SPA assets and /healthz stay open. /docs and /openapi.json
        stay open too — discoverable API spec is standard, the auth gate
        only matters for the endpoints themselves.
        """
        if not auth_cfg.required:
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        authorization = request.headers.get("authorization")
        if auth_cfg.password and _check_basic(authorization, auth_cfg.password):
            return await call_next(request)
        if auth_cfg.token and _check_bearer(authorization, auth_cfg.token):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": 'Basic realm="MissionDebug"'},
        )

    def get_db() -> Db:
        return db

    app.include_router(sessions_router(get_db))
    app.include_router(files_router(get_db))
    app.include_router(annotations_router(get_db))
    # v2 fleet endpoints — agents ingest sessions + post heartbeats.
    app.include_router(ingest_router(get_db, telemetry, alerter))
    app.include_router(agents_router(get_db))
    # v2 P3.5.2 — similarity search ("Has this happened before?").
    app.include_router(similarity_router(get_db))
    # v2 P3.5.6 — per-session resolution records (status + root cause).
    app.include_router(resolutions_router(get_db, telemetry))
    # v2 P3.5.6b — fleet incident dashboard rollup (captures, MTTR,
    # resolution rate, recurrence, top patterns).
    app.include_router(fleet_stats_router(get_db))
    # v2 incident agent — POST /api/v2/incidents/ask
    app.include_router(incidents_router(incident_agent))

    @app.api_route(
        "/healthz",
        methods=["GET", "HEAD"],
        tags=["system"],
        summary="Liveness probe",
    )
    def healthz():
        """Returns `{"ok": true}` when the backend is up. Use as your container/systemd healthcheck."""
        return {"ok": True}

    @app.post("/api/admin/rescan", tags=["admin"], summary="Rescan sessions directory for new MCAPs")
    def rescan():
        """Force an immediate scan of the configured sessions directory. Normally happens every 5s."""
        n = scan_directory(sessions_dir, db)
        return {"indexed": n}

    @app.api_route(
        "/api/admin/disk",
        methods=["GET", "HEAD"],
        tags=["admin"],
        summary="Disk usage + retention cap",
    )
    def disk_usage():
        """Total MCAP bytes on disk, configured cap, and whether retention is enabled."""
        cap_bytes = max_disk_mb * 1024 * 1024
        used = db.total_mcap_bytes()
        return {
            "used_bytes": used,
            "used_mb": round(used / 1e6, 2),
            "cap_mb": max_disk_mb,
            "cap_enabled": cap_bytes > 0,
            "session_count": len(db.list_sessions(limit=10**9)),
            # v2 P5b lifecycle policy state (0 = disabled).
            "cold_after_days": cold_after_days,
            "delete_after_days": delete_after_days,
            "lifecycle_enabled": cold_after_days > 0 or delete_after_days > 0,
        }

    @app.post("/api/admin/sweep", tags=["admin"], summary="Force a retention sweep")
    def sweep():
        cap_bytes = max_disk_mb * 1024 * 1024
        result = sweep_once(db, cap_bytes)
        return {
            "deleted_ids": result.deleted_ids,
            "bytes_freed": result.bytes_freed,
            "bytes_after": result.bytes_after,
            "cap_bytes": result.cap_bytes,
        }

    @app.get(
        "/api/admin/alerts",
        tags=["admin"],
        summary="Alerting status (which webhook destinations are configured)",
    )
    def alerts_status():
        return {"enabled": alerter.enabled}

    @app.post(
        "/api/admin/alerts/test",
        tags=["admin"],
        summary="Send a synthetic test alert to the configured destinations",
    )
    def alerts_test():
        """Fire a test incident alert so operators can verify their Slack /
        PagerDuty / webhook config without waiting for a real detector.
        Returns per-destination delivery results. Synchronous (unlike the
        ingest path) so the caller sees whether delivery succeeded."""
        event = AlertEvent(
            session_id="test-alert",
            robot_id="test-robot",
            subsystem="diagnostics",
            rule="alert_test",
            summary="This is a MissionDebug test alert. If you see it, alerting works.",
            prior_occurrences=0,
        )
        deliveries = alerter.alert_capture(event)
        return {
            "enabled": alerter.enabled,
            "deliveries": [
                {"destination": d.destination, "ok": d.ok, "detail": d.detail}
                for d in deliveries
            ],
        }

    @app.post(
        "/api/admin/lifecycle/sweep",
        tags=["admin"],
        summary="Force an age-based lifecycle sweep (cold-tier + delete)",
    )
    def lifecycle_sweep():
        """Apply the configured `cold_after_days` / `delete_after_days`
        policies immediately. Normally runs hourly. No-op for disabled
        policies."""
        result = sweep_lifecycle_once(
            db,
            cold_after_days=cold_after_days,
            delete_after_days=delete_after_days,
        )
        return {
            "cooled_ids": result.cooled_ids,
            "deleted_ids": result.deleted_ids,
            "cold_after_days": result.cold_after_days,
            "delete_after_days": result.delete_after_days,
        }

    # Mount the web UI's static dist if provided. Done last so it doesn't
    # shadow /api/* or /healthz. The web app uses relative /api URLs, so
    # serving it from the same origin avoids CORS + a separate static
    # server entirely.
    if web_dir is not None and web_dir.exists():
        app.mount("/", SpaStaticFiles(directory=str(web_dir), html=True), name="web")
        log.info("Serving web UI from %s", web_dir)

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
        default=_env_int("MD_MAX_DISK_MB"),
        help="Cap on total MCAP bytes; oldest sessions deleted when over. 0 = disabled.",
    )
    parser.add_argument(
        "--cold-after-days",
        type=int,
        default=_env_int("MD_COLD_AFTER_DAYS"),
        help=(
            "Release MCAP bytes for sessions older than N days, keeping the "
            "incident metadata (the 'recording unavailable' state). 0 = disabled."
        ),
    )
    parser.add_argument(
        "--delete-after-days",
        type=int,
        default=_env_int("MD_DELETE_AFTER_DAYS"),
        help="Purge sessions (row + file) older than N days. 0 = disabled.",
    )
    parser.add_argument(
        "--web-dir",
        default=os.environ.get("MD_WEB_DIR", ""),
        help="Directory of the built web UI to serve at /. Empty = don't serve UI.",
    )
    args = parser.parse_args()

    fixtures = (
        Path(args.fixtures_dir) if os.environ.get("MD_FIXTURES") == "1" else None
    )
    # v2 P4 — read auth env once and enforce Hard Rule 21 at startup.
    # Fleet mode without a password raises SystemExit so docker/systemd
    # surfaces a clear startup failure instead of running insecure.
    auth_cfg = AuthConfig()
    auth_cfg.enforce_startup_invariants()

    app = build_app(
        Path(args.sessions_dir),
        Path(args.db),
        fixtures_dir=fixtures,
        max_disk_mb=args.max_disk_mb,
        cold_after_days=args.cold_after_days,
        delete_after_days=args.delete_after_days,
        web_dir=Path(args.web_dir) if args.web_dir else None,
        auth_config=auth_cfg,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
