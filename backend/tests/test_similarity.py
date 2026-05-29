"""v2 P3.5.2 — TF-IDF similarity over structured summaries.

Two layers tested here:

1. The pure-function core (tokenize / build_idf / cosine / rank_similar)
   in isolation. These are the determinism + correctness invariants
   downstream features (similarity panel, dashboard recurrence KPI)
   depend on.

2. The HTTP route, hitting the real SQLite DB through TestClient.
   Verifies the strictly-past filter, the empty-corpus path, k clamping,
   and that the response surfaces the fields the UI needs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from missiondebug_backend.main import build_app
from missiondebug_backend.similarity import (
    build_idf,
    cosine,
    rank_similar,
    tfidf_vec,
    tokenize,
)


# ============================================================
# Pure-function layer
# ============================================================


def test_tokenize_lowercases_and_strips_stopwords():
    tokens = tokenize("Auto-triggered by rule 'stall' on robot-001")
    # 'by', 'on' are stopwords; 'auto', 'triggered', 'rule' are template noise.
    # Surviving signal: 'stall', 'robot-001' (as 'robot', '001' after - split).
    assert "stall" in tokens
    assert "by" not in tokens
    assert "rule" not in tokens
    assert "auto" not in tokens


def test_tokenize_preserves_ros_topic_paths():
    """`/cmd_vel` is one token, not two. Topic names are high-signal
    identifiers — splitting them on `/` would collapse them into the
    generic `cmd` and `vel` and destroy the lexical match."""
    tokens = tokenize("Captured 60s across 1 topic: /cmd_vel (180 msgs)")
    assert "/cmd_vel" in tokens


def test_tokenize_empty_input():
    assert tokenize("") == []
    assert tokenize(None) == []  # type: ignore[arg-type]


def test_build_idf_smoothing_handles_corpus_of_one():
    """A new fleet's first capture is a corpus of 1. The IDF must not
    divide by zero or produce NaN."""
    idf = build_idf([["stall", "robot"]])
    assert all(v > 0 for v in idf.values())
    assert not any(v != v for v in idf.values())  # no NaN


def test_build_idf_rare_terms_score_higher():
    """A term in 1/5 docs should score higher than one in 5/5."""
    docs = [
        ["common", "rare1"],
        ["common"],
        ["common"],
        ["common"],
        ["common"],
    ]
    idf = build_idf(docs)
    assert idf["rare1"] > idf["common"]


def test_tfidf_vec_normalizes_by_doc_length():
    """A short doc with one occurrence of 'stall' should score 'stall'
    higher (per term) than a long doc with one occurrence — TF is
    normalized so longer captures don't dominate cosine."""
    idf = {"stall": 1.0}
    short = tfidf_vec(["stall"], idf)
    long_doc = tfidf_vec(["stall"] + ["filler"] * 9, idf)  # filler has no idf entry → 0
    assert short["stall"] > long_doc["stall"]


def test_cosine_identical_vectors_equals_one():
    v = {"a": 0.5, "b": 0.3}
    assert abs(cosine(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors_equals_zero():
    a = {"x": 1.0}
    b = {"y": 1.0}
    assert cosine(a, b) == 0.0


def test_cosine_empty_returns_zero():
    """Stopword-only docs leave an empty vector; cosine must not blow up."""
    assert cosine({}, {"a": 1.0}) == 0.0
    assert cosine({"a": 1.0}, {}) == 0.0


# ============================================================
# Ranking — the integration layer the route depends on
# ============================================================


def test_rank_returns_most_similar_first():
    query = "Auto-triggered by rule 'stall' on robot-001 — /cmd_vel"
    candidates = [
        ("ses_old_path", "Auto-triggered by rule 'path-deviation' on robot-002 — /plan"),
        ("ses_old_stall", "Auto-triggered by rule 'stall' on robot-003 — /cmd_vel"),
        ("ses_battery", "Auto-triggered by rule 'battery-low' on robot-007 — /battery"),
    ]
    ranked = rank_similar(query, candidates, k=3)
    assert ranked[0][0] == "ses_old_stall"
    # All non-zero scored, but stall should be highest.
    assert ranked[0][1] > ranked[1][1]


def test_rank_filters_zero_score_candidates():
    """A candidate with no shared signal tokens shouldn't be surfaced as
    'similar' — even at 0%. Returning it would be UX noise."""
    query = "Auto-triggered by rule 'stall' on robot-001 — /cmd_vel"
    candidates = [
        ("ses_unrelated", "Manual save at warehouse-2 — /battery /odom"),
    ]
    ranked = rank_similar(query, candidates, k=3)
    # No shared signal tokens with the query (different rule, different
    # topics) → no result. Better to show empty than fake matches.
    assert all(score > 0 for _, score in ranked)


def test_rank_respects_k():
    query = "stall stall /cmd_vel"
    candidates = [(f"s{i}", "stall /cmd_vel") for i in range(10)]
    ranked = rank_similar(query, candidates, k=3)
    assert len(ranked) == 3


def test_rank_deterministic_tie_break_by_id():
    """HR27: when two candidates have identical text (and therefore
    identical scores), the result order is stable across runs. Caller
    code can rely on this for embedding-based KPIs."""
    query = "stall /cmd_vel"
    candidates = [
        ("s_zebra", "stall /cmd_vel"),
        ("s_alpha", "stall /cmd_vel"),
    ]
    r1 = rank_similar(query, candidates, k=2)
    r2 = rank_similar(query, candidates, k=2)
    assert r1 == r2
    # Ascending id tie-break.
    assert r1[0][0] == "s_alpha"


def test_rank_empty_query_returns_empty():
    assert rank_similar("", [("s1", "stall")], k=3) == []
    assert rank_similar("stall", [], k=3) == []
    assert rank_similar("stall", [("s1", "stall")], k=0) == []


def test_rank_stopword_only_query_returns_empty():
    """A summary made entirely of template noise has nothing to match on."""
    query = "Manual save with topics captured"  # all stopwords
    candidates = [("s1", "stall /cmd_vel")]
    assert rank_similar(query, candidates, k=3) == []


# ============================================================
# HTTP route
# ============================================================


def _client(tmp_path) -> TestClient:
    app = build_app(
        sessions_dir=tmp_path / "sessions",
        db_path=tmp_path / "x.sqlite3",
    )
    return TestClient(app)


def _ingest(client: TestClient, *, sid: str, started_at: int, summary: str | None, label: str = "anomaly:x") -> None:
    client.post(
        "/api/v1/sessions/ingest",
        json={
            "session_id": sid,
            "robot_id": "robot-001",
            "started_at": started_at,
            "ended_at": started_at + 60_000,
            "duration_ms": 60_000,
            "label": label,
            "topics": ["/cmd_vel"],
            "mcap_size_bytes": 1024,
            "mcap_url": f"http://x/{sid}",
            "summary": summary,
        },
    )


def test_similar_endpoint_returns_past_matches(tmp_path):
    c = _client(tmp_path)
    _ingest(c, sid="s_old_stall", started_at=1_000, summary="rule 'stall' on robot-001 /cmd_vel")
    _ingest(c, sid="s_old_path", started_at=2_000, summary="rule 'path-deviation' on robot-001 /plan")
    _ingest(c, sid="s_query", started_at=3_000, summary="rule 'stall' on robot-001 /cmd_vel")

    r = c.get("/api/v2/sessions/s_query/similar?k=3")
    assert r.status_code == 200
    body = r.json()
    ids = [s["session_id"] for s in body["similar"]]
    # s_old_stall must rank first (high lexical overlap with query).
    # s_old_path is included only if it has non-zero shared signal —
    # in this fixture it shares 'rule' (stopword, dropped) and 'robot-001'
    # so it may or may not appear. We assert ordering when both present.
    assert ids[0] == "s_old_stall"
    assert body["similar"][0]["score"] > 0
    # Each result carries the summary so the UI can render an excerpt.
    assert body["similar"][0]["summary"] is not None


def test_similar_excludes_future_sessions(tmp_path):
    """A capture that happened AFTER the query session is not a 'past
    incident' and must not appear in the result."""
    c = _client(tmp_path)
    _ingest(c, sid="s_query", started_at=1_000, summary="rule 'stall' /cmd_vel")
    _ingest(c, sid="s_future", started_at=2_000, summary="rule 'stall' /cmd_vel")

    r = c.get("/api/v2/sessions/s_query/similar?k=3")
    assert r.status_code == 200
    ids = [s["session_id"] for s in r.json()["similar"]]
    assert "s_future" not in ids


def test_similar_excludes_self(tmp_path):
    c = _client(tmp_path)
    _ingest(c, sid="s_query", started_at=1_000, summary="rule 'stall' /cmd_vel")
    r = c.get("/api/v2/sessions/s_query/similar?k=3")
    assert r.status_code == 200
    assert r.json()["similar"] == []


def test_similar_query_without_summary_returns_empty(tmp_path):
    """Older sessions ingested before the summarizer existed have
    summary=null. The endpoint short-circuits gracefully."""
    c = _client(tmp_path)
    _ingest(c, sid="s_old", started_at=500, summary="rule 'stall' /cmd_vel")
    _ingest(c, sid="s_query", started_at=1_000, summary=None)

    r = c.get("/api/v2/sessions/s_query/similar?k=3")
    assert r.status_code == 200
    body = r.json()
    assert body["similar"] == []
    assert "reason" in body


def test_similar_404_on_unknown_session(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/v2/sessions/nonexistent/similar")
    assert r.status_code == 404


def test_similar_k_param_validated(tmp_path):
    c = _client(tmp_path)
    _ingest(c, sid="s_query", started_at=1_000, summary="rule 'stall' /cmd_vel")
    # k must be >= 1
    assert c.get("/api/v2/sessions/s_query/similar?k=0").status_code == 422
    # k must be <= 20 (guardrail on response size)
    assert c.get("/api/v2/sessions/s_query/similar?k=100").status_code == 422


def test_similar_skips_summaryless_candidates(tmp_path):
    """A past session without a summary cannot participate in similarity —
    we have nothing to embed. It's silently excluded, not surfaced as
    a 0-score result."""
    c = _client(tmp_path)
    _ingest(c, sid="s_no_summary", started_at=500, summary=None)
    _ingest(c, sid="s_with_summary", started_at=600, summary="rule 'stall' /cmd_vel")
    _ingest(c, sid="s_query", started_at=1_000, summary="rule 'stall' /cmd_vel")

    r = c.get("/api/v2/sessions/s_query/similar?k=3")
    ids = [s["session_id"] for s in r.json()["similar"]]
    assert "s_no_summary" not in ids
    assert "s_with_summary" in ids
