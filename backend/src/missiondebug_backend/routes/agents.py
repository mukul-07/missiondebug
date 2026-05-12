"""Agent heartbeat + health endpoints.

Agents ping POST /api/v1/agents/heartbeat every 60s. The hub tracks
last_heartbeat in the agents table and writes a history row in
agent_heartbeats. Phase 2 uses these to compute fleet operational
health; Phase 1 just needs the plumbing in place.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ..db import AgentRow, Db


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

    @router.get(
        "",
        summary="List all known agents (Phase 2 health page reads this)",
    )
    def list_agents(db: Db = Depends(get_db)) -> dict:
        agents = db.list_agents()
        return {"agents": [AgentInfo.from_row(a).model_dump() for a in agents]}

    return router
