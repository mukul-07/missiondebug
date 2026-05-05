from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import Db, SessionRow


def get_router(get_db) -> APIRouter:
    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    def _serialize(row: SessionRow) -> dict:
        return {
            "id": row.id,
            "robot_id": row.robot_id,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "duration_ms": row.duration_ms,
            "label": row.label,
            "mcap_size_bytes": row.mcap_size_bytes,
            "topics": row.topics,
            "created_at": row.created_at,
        }

    @router.get("")
    def list_sessions(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        db: Db = Depends(get_db),
    ):
        rows = db.list_sessions(limit=limit, offset=offset)
        return {"sessions": [_serialize(r) for r in rows]}

    @router.get("/{session_id}")
    def get_session(session_id: str, db: Db = Depends(get_db)):
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, "session not found")
        return _serialize(row)

    return router
