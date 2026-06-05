"""v2 P3.5.6 — session resolution endpoints.

A "resolution" is the operator's after-action record on a captured
incident: status (open/investigating/resolved/duplicate/wont_fix),
root cause, optional ticket link, optional duplicate-of pointer.

The data shape here drives two business-facing KPIs on the fleet
incident dashboard:
- Resolution rate: % of sessions over a window in a terminal status
- MTTR (mean time to resolution): mean(resolved_at - started_at)
  across sessions that reached a terminal state in the window

And surfaces day-to-day in two places:
- "Resolution" panel on session detail (P3.5.6c)
- "Has this happened before?" panel (P3.5.5) shows the resolution
  status of each similar past session, so an operator can see at a
  glance: "this one was resolved 2 weeks ago — and here's the fix"

Endpoints are deliberately small surface: GET, PUT, DELETE. No PATCH —
an upsert is conceptually a full-row replacement at the resolution
granularity, and PATCH would invite partial-update races without
solving a real problem.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import RESOLUTION_STATUSES, Db, ResolutionRow
from ..telemetry import Telemetry

_TERMINAL_STATUSES = {"resolved", "duplicate", "wont_fix"}


def _serialize(r: ResolutionRow) -> dict:
    return {
        "session_id": r.session_id,
        "status": r.status,
        "root_cause": r.root_cause,
        "linked_ticket": r.linked_ticket,
        "duplicate_of": r.duplicate_of,
        "resolved_at": r.resolved_at,
        "edited_by": r.edited_by,
        "edited_at": r.edited_at,
    }


class ResolutionPayload(BaseModel):
    """Body for PUT. status is required; everything else is optional and
    nullable (so a caller can clear root_cause by sending null)."""

    status: str = Field(
        description=f"One of: {', '.join(RESOLUTION_STATUSES)}",
        pattern="^(open|investigating|resolved|duplicate|wont_fix)$",
    )
    root_cause: str | None = Field(default=None, max_length=4000)
    linked_ticket: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "Free-form: a URL, a Jira/Linear id, anything an operator wants "
            "to link this incident to. No format validation — fleets use "
            "different ticketing systems."
        ),
    )
    duplicate_of: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "The session_id of the canonical incident this one duplicates. "
            "Required when status='duplicate'; ignored otherwise."
        ),
    )
    edited_by: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Optional operator identifier. Until P7 (SSO/RBAC) the hub "
            "doesn't know who the caller is; the client can pass this "
            "for audit logging. Stays optional so single-password mode "
            "still works."
        ),
    )


def get_router(get_db, telemetry: Telemetry | None = None) -> APIRouter:
    router = APIRouter(
        prefix="/api/v2/sessions",
        tags=["resolutions"],
    )
    telemetry = telemetry or Telemetry()

    @router.get(
        "/{session_id}/resolution",
        summary="Get this session's resolution (or implicit 'open')",
    )
    def get_resolution(session_id: str, db: Db = Depends(get_db)) -> dict:
        """Returns the resolution row if one exists. If the session has no
        resolution yet, returns the implicit 'open' shape with edited_at=0
        so the UI doesn't have to branch on null. 404 only if the session
        itself doesn't exist."""
        session = db.get_session(session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        existing = db.get_resolution(session_id)
        if existing is not None:
            return _serialize(existing)
        return _serialize(ResolutionRow.implicit_open(session_id))

    @router.put(
        "/{session_id}/resolution",
        summary="Set or replace this session's resolution",
    )
    def put_resolution(
        session_id: str,
        payload: ResolutionPayload,
        db: Db = Depends(get_db),
    ) -> dict:
        """Upsert the resolution. Auto-stamps resolved_at on the first
        transition into a terminal status; clears it if moved back to
        non-terminal; preserves the original timestamp on terminal→terminal
        transitions (MTTR measures time-to-first-resolution).

        Validates that duplicate_of, when set, references an existing
        session — otherwise the dashboard's "duplicate" rollups would
        accumulate dangling pointers.
        """
        if db.get_session(session_id) is None:
            raise HTTPException(404, "session not found")

        if payload.status == "duplicate":
            if not payload.duplicate_of:
                raise HTTPException(
                    422, "duplicate_of is required when status='duplicate'"
                )
            if payload.duplicate_of == session_id:
                raise HTTPException(
                    422, "duplicate_of cannot reference the session itself"
                )
            if db.get_session(payload.duplicate_of) is None:
                raise HTTPException(
                    422,
                    f"duplicate_of references unknown session "
                    f"{payload.duplicate_of!r}",
                )

        # Capture the prior status BEFORE the upsert so we can detect the
        # first transition into a terminal state (matches MTTR's
        # time-to-first-resolution semantics — count each incident once).
        prev = db.get_resolution(session_id)
        was_terminal = prev is not None and prev.status in _TERMINAL_STATUSES

        row = db.upsert_resolution(
            session_id=session_id,
            status=payload.status,
            root_cause=payload.root_cause,
            linked_ticket=payload.linked_ticket,
            duplicate_of=payload.duplicate_of if payload.status == "duplicate" else None,
            edited_by=payload.edited_by,
        )

        # v2 OTel — count an incident as "resolved" only on its first move
        # into a terminal status. No-op unless export is configured.
        if payload.status in _TERMINAL_STATUSES and not was_terminal:
            try:
                telemetry.record_resolution(status=payload.status)
            except Exception:  # pragma: no cover - defensive
                pass

        return _serialize(row)

    @router.delete(
        "/{session_id}/resolution",
        status_code=204,
        summary="Revert to the implicit 'open' status by deleting the row",
    )
    def delete_resolution(session_id: str, db: Db = Depends(get_db)) -> None:
        if db.get_session(session_id) is None:
            raise HTTPException(404, "session not found")
        db.delete_resolution(session_id)

    return router
