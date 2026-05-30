"""Local FastAPI control API for the agent.

Single endpoint for v0: POST /sessions/save flushes the ring buffer to a
new MCAP file. Adds GET /healthz for liveness.
"""

from __future__ import annotations

import logging
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

    return SaveResponse(
        session_id=meta.session_id,
        path=meta.path,
        duration_s=meta.duration_ns / 1e9,
        topics=meta.topics,
        size_bytes=meta.size_bytes,
        label=meta.label,
        summary=summary,
    )


def build_app(
    config: AgentConfig,
    ring: RingBuffer,
    *,
    schema_loader: Callable[[str], str] | None = None,
    hub_client: "HubClient | None" = None,
    s3_uploader: "S3Uploader | None" = None,
) -> FastAPI:
    app = FastAPI(
        title="MissionDebug Agent",
        description=(
            "Local control plane for the agent. The agent runs on the robot, "
            "maintains a 60s rolling buffer of selected ROS 2 topics, and "
            "writes an MCAP file when a detector fires or when this API is "
            "called. Bind to loopback only — there is no authentication."
        ),
        version="1.5.0",
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
        )

    return app
