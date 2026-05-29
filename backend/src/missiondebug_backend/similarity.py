"""Lightweight similarity search over structured session summaries.

v2 P3.5.2 — the "Has this happened before?" substrate. Given a session's
summary text, find the top K past sessions whose summaries share token
context (rule name, robot, subsystem, topics, key telemetry tokens).

This is the foundation for:
- Per-session "Has this happened before?" panel on SessionDetail
- Fleet incident dashboard recurrence-rate KPI
- Auto-grouping captures into known failure patterns

Design choices (re-validated 2026-05-29):

- **Pure-Python TF-IDF + cosine.** No sklearn, no numpy, no scipy. The
  structured summaries are short (~200 chars) and the corpus per fleet
  is small (<10K sessions in v2). A pure-Python implementation scans
  the whole corpus in well under 50ms even at the upper bound, which is
  fine for an on-demand endpoint that runs once per session-open.
  Adding sklearn would balloon the hub install by ~50-150MB for a tiny
  gain. If/when we ship LLM narratives (P3.5.1b) the summaries become
  longer and lexical match degrades — that's when we upgrade to
  fastembed dense embeddings, behind the same opt-in flag as the LLM.

- **No precomputed embedding column on `sessions`.** Computing the
  vectors on the fly means: zero new schema migrations, zero backfill
  jobs, no "stale embedding after summary regen" failure mode. The
  trade-off is O(N) per query — fine at this scale, revisit if we
  ever cross 10K sessions per fleet (we can swap to sqlite-vec
  without touching the API contract).

- **TF-IDF formula:** smoothed IDF `log((N+1)/(df+1)) + 1`. Same as
  sklearn's default. Survives a corpus of 1.

- **Strictly past sessions only.** "Has this happened before?" means
  we surface PRIOR incidents, never future ones, never the query
  session itself. Filter is `started_at < query.started_at`.

Hard Rule 27: deterministic. Same corpus + query → same ranking. Stable
sort tie-break by session_id.

Hard Rule 25: lightweight. No new pip deps. Runs offline.
"""

from __future__ import annotations

import math
import re
from collections import Counter


# Token shape: lowercase alphanumeric runs, allowing `/` and `_` so ROS
# topic names like `/cmd_vel` survive as single tokens. We do NOT split
# on `/` — `cmd_vel` and `/cmd_vel` are different signals in practice.
_WORD_RE = re.compile(r"[a-z0-9_/]+")

# Stopwords: tokens that appear in nearly every summary (because they
# come from the summarizer's fixed template) carry no signal and dilute
# cosine similarity. Tuned for the structured summary format produced by
# agent/summarizer.py — additions here invalidate cached test snapshots,
# so update tests when extending this set.
_STOPWORDS = frozenset({
    "a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the",
    "to", "with", "was", "is", "were", "are", "be", "been",
    # Summarizer template noise — every summary has these:
    "captured", "across", "total", "payload", "msgs", "topic", "topics",
    "auto", "triggered", "rule", "manual", "save", "subsystem",
    "utc",
})

_MIN_TOKEN_LEN = 2


def tokenize(text: str) -> list[str]:
    """Tokenize a summary string into normalized signal-bearing terms.

    Lowercases, splits on word boundaries, strips stopwords and very
    short tokens. Order-preserving (TF counts care, IDF doesn't).
    """
    if not text:
        return []
    return [
        t for t in _WORD_RE.findall(text.lower())
        if len(t) >= _MIN_TOKEN_LEN and t not in _STOPWORDS
    ]


def build_idf(docs: list[list[str]]) -> dict[str, float]:
    """Smoothed inverse document frequency over a corpus.

    `idf(t) = log((N + 1) / (df(t) + 1)) + 1` — same as sklearn's default.
    The `+1` smoothing keeps the formula sane for tiny corpora (N=1 still
    gives meaningful weights instead of dividing by zero).
    """
    n = len(docs)
    df: Counter[str] = Counter()
    for doc in docs:
        for t in set(doc):
            df[t] += 1
    return {t: math.log((n + 1) / (count + 1)) + 1 for t, count in df.items()}


def tfidf_vec(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """TF-IDF vector for a single document, sparse (only non-zero terms).

    Normalized TF (count / doc length) so longer summaries don't dominate.
    """
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {t: (c / total) * idf.get(t, 0.0) for t, c in counts.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two sparse TF-IDF vectors. Returns 0 if
    either is empty or has zero norm (e.g. document made entirely of
    stopwords)."""
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) < len(b) else (b, a)
    dot = sum(v * large.get(t, 0.0) for t, v in small.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank_similar(
    query_summary: str,
    candidates: list[tuple[str, str]],
    k: int,
) -> list[tuple[str, float]]:
    """Return top K `(session_id, score)` pairs ranked by cosine similarity.

    `candidates` is `[(session_id, summary), ...]` for the corpus we're
    searching against. Caller is responsible for filtering candidates
    (typically: past sessions only, non-null summary, not the query
    session itself).

    Zero-score candidates (no shared tokens after stopword removal) are
    dropped — surfacing them as "0% similar" would be noise. Tie-break
    on session_id ascending so identical corpora always produce identical
    rankings (Hard Rule 27).
    """
    if not query_summary or not candidates or k <= 0:
        return []
    query_tokens = tokenize(query_summary)
    if not query_tokens:
        return []
    candidate_tokens = [tokenize(s) for _, s in candidates]
    # IDF must be computed over the union so query and candidates share
    # the same term-weighting space.
    idf = build_idf([query_tokens] + candidate_tokens)
    query_vec = tfidf_vec(query_tokens, idf)
    scored: list[tuple[str, float]] = []
    for (sid, _), tokens in zip(candidates, candidate_tokens):
        score = cosine(query_vec, tfidf_vec(tokens, idf))
        if score > 0.0:
            scored.append((sid, score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:k]
