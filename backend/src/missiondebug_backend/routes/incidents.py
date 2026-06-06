"""Natural-language incident-agent endpoint (v2).

POST /api/v2/incidents/ask { "question": "..." }
  -> { enabled, answer, citations: [session_id...], tools_used: [...] }

Opt-in: when no LLM key is configured the agent reports enabled=false and a
helpful message; the rest of the app is unaffected (HR24). See
incident_agent.py for the tool surface and the bounded-data guarantees.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..incident_agent import IncidentAgent


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def get_router(incident_agent: IncidentAgent) -> APIRouter:
    router = APIRouter(prefix="/api/v2/incidents", tags=["incident-agent"])

    @router.get("/agent", summary="Whether the natural-language agent is enabled")
    def agent_status() -> dict:
        return incident_agent.status()

    @router.post("/ask", summary="Ask the fleet's incident history a question")
    def ask(req: AskRequest) -> dict:
        return incident_agent.ask(req.question)

    return router
