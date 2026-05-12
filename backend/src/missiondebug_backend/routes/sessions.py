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

    @router.get("", summary="List sessions, newest first")
    def list_sessions(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        robot_id: str | None = Query(None),
        db: Db = Depends(get_db),
    ):
        """Returns sessions plus the set of known robot_ids for the filter dropdown."""
        rows = db.list_sessions(limit=limit, offset=offset, robot_id=robot_id)
        counts = db.annotation_counts()
        return {
            "sessions": [
                {**_serialize(r), "annotation_count": counts.get(r.id, 0)}
                for r in rows
            ],
            "robots": db.list_robot_ids(),
        }

    @router.get("/{session_id}", summary="Get a single session by id")
    def get_session(session_id: str, db: Db = Depends(get_db)):
        """Returns the session row plus its annotation count. 404 if not found."""
        row = db.get_session(session_id)
        if row is None:
            raise HTTPException(404, "session not found")
        counts = db.annotation_counts()
        return {**_serialize(row), "annotation_count": counts.get(row.id, 0)}

    return router
