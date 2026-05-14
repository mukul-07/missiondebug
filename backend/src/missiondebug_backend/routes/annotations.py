from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..db import AnnotationRow, Db


class AnnotationCreate(BaseModel):
    time_ns: int = Field(ge=0)
    body: str = Field(min_length=1, max_length=2000)


class AnnotationUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


def _serialize(a: AnnotationRow) -> dict:
    return {
        "id": a.id,
        "session_id": a.session_id,
        "time_ns": a.time_ns,
        "body": a.body,
        "created_at": a.created_at,
    }


def get_router(get_db) -> APIRouter:
    router = APIRouter(tags=["annotations"])

    @router.api_route(
        "/api/sessions/{session_id}/annotations",
        methods=["GET", "HEAD"],
        summary="List annotations for a session",
    )
    def list_for_session(session_id: str, db: Db = Depends(get_db)):
        if db.get_session(session_id) is None:
            raise HTTPException(404, "session not found")
        rows = db.list_annotations(session_id)
        return {"annotations": [_serialize(r) for r in rows]}

    @router.post(
        "/api/sessions/{session_id}/annotations",
        status_code=201,
        summary="Create an annotation at a timestamp",
    )
    def create_for_session(
        session_id: str,
        payload: AnnotationCreate,
        db: Db = Depends(get_db),
    ):
        if db.get_session(session_id) is None:
            raise HTTPException(404, "session not found")
        row = db.insert_annotation(session_id, payload.time_ns, payload.body.strip())
        return _serialize(row)

    @router.put("/api/annotations/{annotation_id}", summary="Update an annotation's body")
    def update(
        annotation_id: int,
        payload: AnnotationUpdate,
        db: Db = Depends(get_db),
    ):
        row = db.update_annotation(annotation_id, payload.body.strip())
        if row is None:
            raise HTTPException(404, "annotation not found")
        return _serialize(row)

    @router.delete("/api/annotations/{annotation_id}", status_code=204, summary="Delete an annotation")
    def delete(annotation_id: int, db: Db = Depends(get_db)):
        if not db.delete_annotation(annotation_id):
            raise HTTPException(404, "annotation not found")
        return Response(status_code=204)

    return router
