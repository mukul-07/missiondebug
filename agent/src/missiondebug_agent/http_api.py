"""Local FastAPI control API for the agent.

Single endpoint for v0: POST /sessions/save flushes the ring buffer to a
new MCAP file. Adds GET /healthz for liveness.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import AgentConfig
from .mcap_writer import SessionMetadata, write_session
from .ring_buffer import RingBuffer

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


def _filename(robot_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{robot_id}_{ts}.mcap"


def save_now(
    config: AgentConfig,
    ring: RingBuffer,
    *,
    label: str | None = None,
    schema_loader: Callable[[str], str] | None = None,
) -> SaveResponse:
    """Flush the ring buffer to a new MCAP file. Returns SaveResponse.

    Used both by the HTTP endpoint and by the anomaly callback (no HTTP roundtrip).
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
        **kw,
    )
    log.info(
        "Saved session %s (%d msgs, %.2fs, %s)",
        meta.session_id, len(snap), meta.duration_ns / 1e9, meta.label,
    )
    return SaveResponse(
        session_id=meta.session_id,
        path=meta.path,
        duration_s=meta.duration_ns / 1e9,
        topics=meta.topics,
        size_bytes=meta.size_bytes,
        label=meta.label,
    )


def build_app(
    config: AgentConfig,
    ring: RingBuffer,
    *,
    schema_loader: Callable[[str], str] | None = None,
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
        Returns the session metadata. 409 if the buffer is empty."""
        return save_now(
            config, ring,
            label=(req.label if req else None),
            schema_loader=schema_loader,
        )

    return app
