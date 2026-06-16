"""Local FastAPI control API for the agent.

Single endpoint for v0: POST /sessions/save flushes the ring buffer to a
new MCAP file. Adds GET /healthz for liveness.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import AgentConfig
from .mcap_writer import SessionMetadata, write_session
from .ring_buffer import RingBuffer
from .summarizer import build_summary

if TYPE_CHECKING:
    from .hub_client import HubClient
    from .s3_uploader import S3Uploader

log = logging.getLogger(__name__)


class SaveRequest(BaseModel):
    label: str | None = None


class SaveResponse(BaseModel):
    session_id: str
    path: str
    duration_s: float
    topics: list[str]
    size_bytes: int
    label: str | None
    summary: str | None = None


def _trigger_from_label(label: str | None) -> str:
    """Classify a capture's trigger from its save label.

    Detectors save with `anomaly:*` labels (e.g. "anomaly:stall"); the
    portal "Save buffer now" uses "transitive:*"; manual API saves pass
    null or arbitrary text. Anomaly labels are returned verbatim so a UI
    can show the specific detector; everything else is "manual".
    """
    if label and label.startswith("anomaly:"):
        return label
    return "manual"


class LastSessionCache:
    """Thread-safe holder for the most recent SaveResponse, any trigger.

    `save_now` runs on two threads — the uvicorn request thread (manual
    POST /sessions/save) and the rclpy spin thread (detector callbacks) —
    so reads/writes are lock-guarded. Read by GET /sessions/last. Holds
    only the latest capture; the agent keeps no history across restarts.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: SaveResponse | None = None
        self._saved_at_ms: int | None = None

    def set(self, resp: SaveResponse) -> None:
        with self._lock:
            self._last = resp
            self._saved_at_ms = int(time.time() * 1000)

    def get(self) -> tuple[SaveResponse | None, int | None]:
        with self._lock:
            return self._last, self._saved_at_ms


def _filename(robot_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{robot_id}_{ts}.mcap"


def save_now(
    config: AgentConfig,
    ring: RingBuffer,
    *,
    label: str | None = None,
    schema_loader: Callable[[str], str] | None = None,
    hub_client: "HubClient | None" = None,
    s3_uploader: "S3Uploader | None" = None,
    last_cache: "LastSessionCache | None" = None,
) -> SaveResponse:
    """Flush the ring buffer to a new MCAP file. Returns SaveResponse.

    Used both by the HTTP endpoint and by the anomaly callback (no HTTP roundtrip).
    When `hub_client` is provided, also forwards the session metadata to the
    configured hub (v2 fleet). Hub failures are non-fatal — Hard Rule 18.
    When `s3_uploader` is provided, also uploads the MCAP to S3 (v2 P5a)
    and posts the public S3 URL to the hub instead of the agent's local
    URL. Upload failure is non-fatal — falls back to agent-served URL.
    """
    snap = ring.snapshot()
    if not snap:
        raise HTTPException(status_code=409, detail="ring buffer is empty")

    output_dir = Path(config.output_dir)
    topic_types = {t.name: t.type for t in config.topics}
    path = output_dir / _filename(config.robot_id)

    kw = {}
    if schema_loader is not None:
        kw["schema_loader"] = schema_loader
    meta: SessionMetadata = write_session(
        snap,
        path,
        robot_id=config.robot_id,
        topic_types=topic_types,
        label=label,
        subsystem=config.hub.subsystem,
        **kw,
    )
    log.info(
        "Saved session %s (%d msgs, %.2fs, %s)",
        meta.session_id, len(snap), meta.duration_ns / 1e9, meta.label,
    )

    # v2 P3.5.1 — structured summary (zero-LLM, deterministic). Generated
    # from metadata the agent already has at save time, so it costs ~µs
    # and works fully offline. Forwarded to the hub for the session-list
    # UI and the upcoming embedding pipeline.
    summary = build_summary(
        snap, config,
        label=meta.label,
        duration_ns=meta.duration_ns,
        started_wall_ns=meta.started_wall_ns,
        size_bytes=meta.size_bytes,
    )

    # v2 P5a — optional S3 upload. Run before reporting to the hub so
    # the hub gets the S3 URL if upload succeeds. On failure, mcap_url
    # in the hub payload stays None, hub_client falls back to the
    # agent-served URL it builds from agent_url + session_id.
    s3_url: str | None = None
    if s3_uploader is not None:
        s3_url = s3_uploader.upload(
            local_path=Path(meta.path),
            robot_id=config.robot_id,
            session_id=meta.session_id,
        )

    # v2 (fleet): forward to hub if configured. Non-fatal on failure.
    if hub_client is not None:
        payload: dict = {
            "session_id": meta.session_id,
            "started_at": meta.started_wall_ns // 1_000_000,
            "ended_at": meta.ended_wall_ns // 1_000_000,
            "duration_ms": meta.duration_ns // 1_000_000,
            "label": meta.label,
            "topics": meta.topics,
            "mcap_size_bytes": meta.size_bytes,
            "summary": summary,
        }
        if s3_url is not None:
            payload["mcap_url"] = s3_url
        hub_client.report_session(payload)

    resp = SaveResponse(
        session_id=meta.session_id,
        path=meta.path,
        duration_s=meta.duration_ns / 1e9,
        topics=meta.topics,
        size_bytes=meta.size_bytes,
        label=meta.label,
        summary=summary,
    )

    # Record the most recent capture (any trigger) for GET /sessions/last,
    # which the Transitive shim polls to surface anomaly captures.
    if last_cache is not None:
        last_cache.set(resp)

    return resp


def build_app(
    config: AgentConfig,
    ring: RingBuffer,
    *,
    schema_loader: Callable[[str], str] | None = None,
    hub_client: "HubClient | None" = None,
    s3_uploader: "S3Uploader | None" = None,
    last_cache: "LastSessionCache | None" = None,
) -> FastAPI:
    app = FastAPI(
        title="MissionDebug Agent",
        description=(
            "Local control plane for the agent. The agent runs on the robot, "
            "maintains a 60s rolling buffer of selected ROS 2 topics, and "
            "writes an MCAP file when a detector fires or when this API is "
            "called. Bind to loopback only. There is no authentication."
        ),
        version="0.3.0",
        license_info={"name": "MIT", "url": "https://github.com/mukul-07/missiondebug/blob/main/LICENSE"},
        contact={"name": "MissionDebug", "url": "https://github.com/mukul-07/missiondebug"},
        openapi_tags=[
            {"name": "capture", "description": "Flush the rolling buffer to a session file."},
            {"name": "system", "description": "Liveness + buffer status."},
        ],
    )

    @app.get("/healthz", tags=["system"], summary="Liveness + current buffer size")
    def healthz():
        """Returns `{ok, buffer_size, robot_id}`. buffer_size is the number of buffered messages across all topics."""
        return {"ok": True, "buffer_size": len(ring), "robot_id": config.robot_id}

    @app.post(
        "/sessions/save",
        response_model=SaveResponse,
        tags=["capture"],
        summary="Capture the current buffer as a new session",
    )
    def save_session(req: SaveRequest | None = None) -> SaveResponse:
        """Flush the in-memory rolling buffer to an MCAP file with an optional label.
        Returns the session metadata. 409 if the buffer is empty.

        When the agent is configured with a hub URL, the session metadata is
        also forwarded to the hub (best effort; failures don't affect the
        local save). When s3.bucket is configured, the MCAP is also uploaded
        to S3 and the public URL is reported to the hub.
        """
        return save_now(
            config, ring,
            label=(req.label if req else None),
            schema_loader=schema_loader,
            hub_client=hub_client,
            s3_uploader=s3_uploader,
            last_cache=last_cache,
        )

    @app.get(
        "/sessions/last",
        tags=["capture"],
        summary="Metadata of the most recent capture (any trigger)",
    )
    def last_session():
        """Return the most recent capture's metadata plus `trigger`
        (`manual` or `anomaly:*`) and `saved_at_ms` (epoch millis).

        404 if nothing has been captured since the agent started — the
        agent keeps no session history across restarts. This endpoint is
        additive; older clients that don't call it are unaffected.
        """
        resp, saved_at_ms = last_cache.get() if last_cache else (None, None)
        if resp is None:
            raise HTTPException(status_code=404, detail="no capture yet")
        return {
            "session_id": resp.session_id,
            "saved_at_ms": saved_at_ms,
            "label": resp.label,
            "trigger": _trigger_from_label(resp.label),
            "duration_s": resp.duration_s,
            "topic_count": len(resp.topics),
            "size_bytes": resp.size_bytes,
        }

    return app
