"""v2 P3.5.2 — similarity search over past session summaries.

Given a session_id, returns the top K past sessions whose structured
summaries are most similar by TF-IDF + cosine. Drives the
"Has this happened before?" panel on session detail and feeds the
recurrence-rate KPI on the fleet incident dashboard.

Design notes (see `similarity.py` for the algorithm rationale):

- "Past" means strictly older than the query session's `started_at`.
  We never return the query session itself or any future session — the
  product question is "has THIS specific shape happened BEFORE."
- Zero-score candidates are filtered out by `rank_similar`. An empty
  `similar` array is the correct response when nothing in the corpus
  matches — not an error.
- Sessions without a structured summary are skipped entirely. Older
  sessions ingested before P3.5.1 stay invisible to this endpoint
  until they either get a backfill (future work) or age out.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import Db
from ..similarity import rank_similar


def get_router(get_db) -> APIRouter:
    router = APIRouter(prefix="/api/v2/sessions", tags=["similarity"])

    @router.get(
        "/{session_id}/similar",
        summary='Rank past sessions whose summaries resemble this one',
    )
    def similar(
        session_id: str,
        k: int = Query(3, ge=1, le=20),
        db: Db = Depends(get_db),
    ) -> dict:
        """Top K past sessions ranked by structured-summary similarity.

        Returns `{similar: [...]}` with at most `k` entries. Each entry
        carries the candidate's `session_id`, `score` (0..1), `summary`,
        and the metadata an operator needs to decide whether to dig in
        (label, robot_id, subsystem, started_at).

        If the query session has no summary, or there are no past
        sessions with summaries, the response is an empty list — never
        an error. The UI renders an explanatory empty state.
        """
        query = db.get_session(session_id)
        if query is None:
            raise HTTPException(404, "session not found")
        if not query.summary:
            return {"similar": [], "reason": "query session has no summary"}

        past = db.list_past_sessions_with_summary(
            before_started_at=query.started_at,
            exclude_id=query.id,
        )
        if not past:
            return {"similar": []}

        candidates = [(s.id, s.summary or "") for s in past]
        ranked = rank_similar(query.summary, candidates, k)

        by_id = {s.id: s for s in past}
        return {
            "similar": [
                {
                    "session_id": sid,
                    "score": round(score, 4),
                    "label": by_id[sid].label,
                    "robot_id": by_id[sid].robot_id,
                    "subsystem": by_id[sid].subsystem,
                    "started_at": by_id[sid].started_at,
                    "summary": by_id[sid].summary,
                }
                for sid, score in ranked
            ],
        }

    return router
