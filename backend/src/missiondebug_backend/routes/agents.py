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

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..db import AgentRow, Db, now_ms

# Timeout for proxying GET /topics to an agent. The agent's discovery scan is
# bounded at ~3s, but a cold scan additionally pays rclpy node creation plus
# per-topic type-resolution imports, so the read budget is generous. Anything
# <= 3s would time out on every cold scan.
_TOPICS_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)


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

    @router.get(
        "/{robot_id}/topics",
        summary="Discover ROS topics on one robot (proxied from its agent)",
    )
    async def agent_topics(robot_id: str, db: Db = Depends(get_db)) -> dict:
        """Proxy the agent's GET /topics (agent >= 0.7.0) through the hub so
        the web UI can render live topic discovery: per-topic category,
        recommended flag, resolvable (message package built?), publisher
        count, and large-type hint. Read-only — the hub never writes config
        back to the robot (Hard Rule 22).

        The response is enriched with `last_capture_topics`: the topic list
        of this robot's most recent session in the hub DB, so the UI can
        show "visible on the graph vs present in the last capture". The
        agent has no endpoint exposing its configured capture list, and the
        last capture is the honest hub-local approximation.

        Error mapping:
          404 — robot unknown to the hub (never heartbeated / ingested)
          409 — agent reported no reachable agent_url (e.g. UDS-only)
          426 — agent predates GET /topics (needs agent >= 0.7.0)
          502 — agent unreachable, upstream error, or invalid payload
        """
        a = db.get_agent(robot_id)
        if a is None:
            raise HTTPException(404, f"unknown robot {robot_id!r}")
        if not a.agent_url:
            raise HTTPException(
                409,
                f"agent for {robot_id!r} has not reported a reachable URL "
                "(agent_url); topic discovery needs a direct HTTP route to "
                "the robot",
            )
        upstream = a.agent_url.rstrip("/") + "/topics"
        try:
            async with httpx.AsyncClient(timeout=_TOPICS_TIMEOUT) as client:
                resp = await client.get(upstream)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"agent unreachable: {e}") from e
        if resp.status_code == 404:
            # Pre-0.7.0 agents have no /topics route at all.
            raise HTTPException(
                426,
                "topic discovery requires agent >= 0.7.0 "
                f"(robot {robot_id!r} reports "
                f"{a.agent_version or 'unknown version'})",
            )
        if resp.status_code >= 400:
            raise HTTPException(502, f"agent returned HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise HTTPException(502, "agent returned invalid JSON") from e
        topics = data.get("topics")
        if not isinstance(topics, list):
            raise HTTPException(502, "agent returned an unexpected payload")
        last = db.list_sessions(robot_id=robot_id, limit=1)
        return {
            "robot_id": robot_id,
            "agent_version": a.agent_version,
            "settled": bool(data.get("settled", False)),
            "topics": topics,
            "last_capture_topics": last[0].topics if last else None,
            "last_capture_session_id": last[0].id if last else None,
        }

    return router
