"""Agent heartbeat + health endpoints.

Agents ping POST /api/v1/agents/heartbeat every 60s. The hub tracks
last_heartbeat in the agents table and writes a history row in
agent_heartbeats. Phase 2 reads those to compute fleet operational
health for the GET /health endpoint.

Status thresholds are env-configurable:
  MD_HUB_HEALTHY_TIMEOUT_SEC (default 120)
  MD_HUB_STALE_TIMEOUT_SEC   (default 300)

  healthy: last_heartbeat < HEALTHY_TIMEOUT_SEC ago
  stale:   HEALTHY ≤ last_heartbeat < STALE
  silent:  last_heartbeat ≥ STALE_TIMEOUT_SEC ago, OR never reported
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ..db import AgentRow, Db, now_ms


def _read_timeout(name: str, default_sec: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default_sec
    try:
        n = int(raw)
        return n if n > 0 else default_sec
    except ValueError:
        return default_sec


HEALTHY_TIMEOUT_SEC = _read_timeout("MD_HUB_HEALTHY_TIMEOUT_SEC", 120)
STALE_TIMEOUT_SEC = _read_timeout("MD_HUB_STALE_TIMEOUT_SEC", 300)


def _classify(
    agent: AgentRow,
    now: int,
    *,
    healthy_sec: int = HEALTHY_TIMEOUT_SEC,
    stale_sec: int = STALE_TIMEOUT_SEC,
) -> tuple[str, int]:
    """Compute (status, silence_seconds) for an agent. silence is -1 if the
    agent has never heartbeated (then status is always 'silent')."""
    if agent.last_heartbeat is None:
        return ("silent", -1)
    silence_ms = max(0, now - agent.last_heartbeat)
    silence_s = silence_ms // 1000
    if silence_ms < healthy_sec * 1000:
        return ("healthy", silence_s)
    if silence_ms < stale_sec * 1000:
        return ("stale", silence_s)
    return ("silent", silence_s)


class HeartbeatPayload(BaseModel):
    robot_id: str = Field(min_length=1, max_length=200)
    buffer_size: int | None = Field(default=None, ge=0)
    agent_url: str | None = Field(default=None)
    agent_version: str | None = Field(default=None, max_length=40)


class AgentInfo(BaseModel):
    robot_id: str
    first_seen: int
    last_heartbeat: int | None
    agent_version: str | None
    agent_url: str | None
    subsystem: str | None

    @classmethod
    def from_row(cls, a: AgentRow) -> "AgentInfo":
        return cls(
            robot_id=a.robot_id,
            first_seen=a.first_seen,
            last_heartbeat=a.last_heartbeat,
            agent_version=a.agent_version,
            agent_url=a.agent_url,
            subsystem=a.subsystem,
        )


def get_router(get_db) -> APIRouter:
    router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

    @router.post(
        "/heartbeat",
        status_code=204,
        summary="Agent liveness ping (every 60s by default)",
    )
    def heartbeat(payload: HeartbeatPayload, db: Db = Depends(get_db)) -> Response:
        """Update last_heartbeat for the agent and append a history row.
        Auto-registers the agent on first ping. Returns 204; agents discard
        the response body."""
        db.record_heartbeat(
            robot_id=payload.robot_id,
            buffer_size=payload.buffer_size,
            agent_url=payload.agent_url,
            agent_version=payload.agent_version,
        )
        return Response(status_code=204)

    @router.api_route(
        "",
        methods=["GET", "HEAD"],
        summary="List all known agents (raw roster, no health computation)",
    )
    def list_agents(db: Db = Depends(get_db)) -> dict:
        agents = db.list_agents()
        return {"agents": [AgentInfo.from_row(a).model_dump() for a in agents]}

    @router.api_route(
        "/health",
        methods=["GET", "HEAD"],
        summary="Fleet operational health — which agents are reporting",
    )
    def health(db: Db = Depends(get_db)) -> dict:
        """Returns aggregate counts (healthy/stale/silent/total) and a
        sorted per-agent list with status + silence_seconds. Used by the
        fleet observability dashboard so operators can see at a glance
        which robots aren't reporting.

        Sort order is "most urgent first": silent → stale → healthy,
        and within each bucket the most-silent agents come first.
        """
        agents = db.list_agents()
        now = now_ms()
        healthy = stale = silent = 0
        rows: list[dict] = []
        for a in agents:
            status, silence_s = _classify(a, now)
            if status == "healthy":
                healthy += 1
            elif status == "stale":
                stale += 1
            else:
                silent += 1
            rows.append({
                "robot_id": a.robot_id,
                "status": status,
                "last_heartbeat": a.last_heartbeat,
                "silence_seconds": int(silence_s),
                "first_seen": a.first_seen,
                "agent_version": a.agent_version,
                "agent_url": a.agent_url,
                "subsystem": a.subsystem,
            })
        # Sort: silent first, then stale, then healthy. Within each, most
        # silent on top so the operator's eye lands on the worst case.
        status_order = {"silent": 0, "stale": 1, "healthy": 2}
        rows.sort(key=lambda r: (status_order[r["status"]], -r["silence_seconds"]))
        return {
            "healthy": healthy,
            "stale": stale,
            "silent": silent,
            "total": len(agents),
            "agents": rows,
            "thresholds": {
                "healthy_seconds": HEALTHY_TIMEOUT_SEC,
                "stale_seconds": STALE_TIMEOUT_SEC,
            },
        }

    return router
