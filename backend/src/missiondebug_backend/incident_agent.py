"""Natural-language incident agent — "ask your fleet's incident history".

The ROSA *pattern* (LLM + tools) applied to MissionDebug's incident corpus,
not the nasa-jpl/rosa library (that's for live ROS introspection). An LLM
is given read-only tools over the data the hub already has (search, detail,
similarity, fleet stats) and answers questions with grounded, session-id-
cited responses.

Hard rules honored:
- Opt-in (HR24): inert unless an LLM key is configured. No key -> the
  endpoint reports "disabled" and everything else keeps working offline.
- Bounded (HR26): tools return only incident *metadata* (ids, rule,
  subsystem, timestamps, summary text, resolution text, counts) — never
  MCAP bytes, camera frames, or PII. The schema below is the whole surface.
- Lightweight (HR25): talks to the Messages API directly over httpx (a
  core dep) — no extra SDK to install. `MD_LLM_BASE_URL` makes it work
  against a local / proxied model for air-gapped sites.

BYO-LLM: the operator brings the key (`MD_LLM_API_KEY` / `ANTHROPIC_API_KEY`)
and may pick the model (`MD_LLM_MODEL`).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from .db import Db
from .similarity import rank_similar
from .telemetry import rule_from_label

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_BASE_URL = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"
_MAX_TOOL_ROUNDS = 6
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You are MissionDebug's fleet incident-history assistant. Answer the "
    "operator's question about their robot fleet's past incidents using ONLY "
    "the provided tools — never invent incidents, robots, rules, or fixes. "
    "Cite the specific session ids you relied on (e.g. SES-203). Be concise "
    "and practical: if a similar incident was resolved, surface its root "
    "cause and ticket. If the tools don't have the answer, say so plainly."
)


@dataclass
class LLMConfig:
    api_key: str | None = None
    model: str = _DEFAULT_MODEL
    base_url: str = _DEFAULT_BASE_URL
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> LLMConfig:
        api_key = (
            os.environ.get("MD_LLM_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or None
        )
        return cls(
            api_key=api_key,
            model=os.environ.get("MD_LLM_MODEL", "").strip() or _DEFAULT_MODEL,
            base_url=(
                os.environ.get("MD_LLM_BASE_URL", "").strip() or _DEFAULT_BASE_URL
            ).rstrip("/"),
        )


# ---- tools (read-only, metadata-only) ----------------------------------

_WINDOW_DEFAULT_DAYS = 30
_MS_PER_DAY = 86_400_000


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _brief(db: Db, session) -> dict:
    """Bounded view of one incident — no MCAP, no PII."""
    res = db.get_resolution(session.id)
    return {
        "session_id": session.id,
        "robot_id": session.robot_id,
        "rule": rule_from_label(session.label),
        "subsystem": session.subsystem,
        "started_at": session.started_at,
        "summary": session.summary,
        "status": res.status if res else "open",
        "root_cause": res.root_cause if res else None,
        "linked_ticket": res.linked_ticket if res else None,
    }


def tool_search_incidents(db: Db, *, robot_id=None, rule=None, subsystem=None,
                          days=None, limit=10) -> dict:
    limit = max(1, min(int(limit or 10), 25))
    rows = db.list_sessions(limit=500, robot_id=robot_id, subsystem=subsystem)
    if days:
        cutoff = _now_ms() - int(days) * _MS_PER_DAY
        rows = [r for r in rows if r.started_at >= cutoff]
    if rule:
        needle = str(rule).lower()
        rows = [r for r in rows if r.label and needle in r.label.lower()]
    out = [_brief(db, r) for r in rows[:limit]]
    return {"count": len(out), "incidents": out}


def tool_get_incident(db: Db, *, session_id: str) -> dict:
    s = db.get_session(session_id)
    if s is None:
        return {"error": f"no incident {session_id!r}"}
    b = _brief(db, s)
    b["topics"] = s.topics
    b["duration_ms"] = s.duration_ms
    return b


def tool_find_similar(db: Db, *, session_id: str, k=3) -> dict:
    k = max(1, min(int(k or 3), 10))
    s = db.get_session(session_id)
    if s is None or not s.summary:
        return {"matches": [], "reason": "no such incident or it has no summary"}
    past = db.list_past_sessions_with_summary(
        before_started_at=s.started_at, exclude_id=s.id
    )
    candidates = [(p.id, p.summary) for p in past if p.summary]
    ranked = rank_similar(s.summary, candidates, k)
    matches = []
    for sid, score in ranked:
        m = db.get_session(sid)
        if m is None:
            continue
        brief = _brief(db, m)
        brief["score"] = round(score, 3)
        matches.append(brief)
    return {"matches": matches}


def tool_get_fleet_stats(db: Db, *, window_days=_WINDOW_DEFAULT_DAYS) -> dict:
    from .routes.fleet_stats import (
        _by_robot,
        _mttr,
        _recurrence,
        _resolution_breakdown,
        _top_patterns,
    )

    window_days = max(1, min(int(window_days or _WINDOW_DEFAULT_DAYS), 365))
    now = _now_ms()
    rows = db.list_sessions_in_window(
        started_at_gte=now - window_days * _MS_PER_DAY, started_at_lt=now
    )
    mttr_ms, mttr_n = _mttr(rows)
    return {
        "window_days": window_days,
        "total_incidents": len(rows),
        "resolution": _resolution_breakdown(rows),
        "recurrence": _recurrence(rows),
        "mttr_days": round(mttr_ms / _MS_PER_DAY, 2) if mttr_ms is not None else None,
        "mttr_sample": mttr_n,
        "top_patterns": _top_patterns(rows),
        "by_robot": _by_robot(rows),
    }


# Tool registry: (json schema for the model, python dispatch).
_TOOLS = [
    {
        "name": "search_incidents",
        "description": "Search past incidents by robot, rule, subsystem, or recency.",
        "input_schema": {
            "type": "object",
            "properties": {
                "robot_id": {"type": "string"},
                "rule": {"type": "string", "description": "rule name e.g. battery_low"},
                "subsystem": {"type": "string"},
                "days": {"type": "integer", "description": "only incidents in the last N days"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_incident",
        "description": "Full metadata for one incident: summary, topics, resolution.",
        "input_schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "find_similar",
        "description": "Past incidents most similar to a given one (with their resolutions).",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "k": {"type": "integer"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "get_fleet_stats",
        "description": "Fleet rollup: counts, recurrence rate, MTTR, top patterns, per-robot.",
        "input_schema": {
            "type": "object",
            "properties": {"window_days": {"type": "integer"}},
        },
    },
]

_DISPATCH = {
    "search_incidents": tool_search_incidents,
    "get_incident": tool_get_incident,
    "find_similar": tool_find_similar,
    "get_fleet_stats": tool_get_fleet_stats,
}


def _default_call_model(config: LLMConfig, system: str, messages: list, tools: list) -> dict:
    """POST to the Anthropic Messages API over httpx. base_url is configurable
    so the same path works against a local / proxied model."""
    import httpx

    headers = {
        "x-api-key": config.api_key or "",
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
        **config.extra_headers,
    }
    body = {
        "model": config.model,
        "max_tokens": _MAX_TOKENS,
        "system": system,
        "messages": messages,
        "tools": tools,
    }
    resp = httpx.post(
        config.base_url + "/v1/messages", headers=headers, json=body, timeout=60.0
    )
    resp.raise_for_status()
    return resp.json()


class IncidentAgent:
    """Runs the tool-use loop against the configured model. `call_model` is
    injectable so the loop + tool execution are testable without a real key."""

    def __init__(self, db: Db, config: LLMConfig | None = None, *, call_model=None):
        self._db = db
        self._config = config or LLMConfig.from_env()
        # An injected call_model (tests, or a local model fronted without a
        # key) counts as enabled even with no api_key.
        self._has_custom_model = call_model is not None
        self._call_model = call_model or (
            lambda system, messages, tools: _default_call_model(
                self._config, system, messages, tools
            )
        )

    @property
    def enabled(self) -> bool:
        return bool(self._config.api_key) or self._has_custom_model

    def ask(self, question: str) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "answer": (
                    "The natural-language incident agent is disabled. Set "
                    "MD_LLM_API_KEY (and optionally MD_LLM_MODEL / MD_LLM_BASE_URL) "
                    "to enable it. Structured search and similarity still work."
                ),
                "citations": [],
                "tools_used": [],
            }

        messages: list = [{"role": "user", "content": question}]
        tools_used: list[str] = []
        citations: set[str] = set()

        for _round in range(_MAX_TOOL_ROUNDS):
            try:
                resp = self._call_model(_SYSTEM_PROMPT, messages, _TOOLS)
            except Exception as e:  # network / auth / model error
                log.warning("incident agent model call failed: %s", e)
                return {
                    "enabled": True,
                    "answer": f"The incident agent could not reach the model: {e}",
                    "citations": sorted(citations),
                    "tools_used": tools_used,
                    "error": True,
                }

            content = resp.get("content", [])
            stop = resp.get("stop_reason")

            if stop != "tool_use":
                text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                return {
                    "enabled": True,
                    "answer": text.strip(),
                    "citations": sorted(citations),
                    "tools_used": tools_used,
                }

            # Execute every tool_use block; feed results back.
            messages.append({"role": "assistant", "content": content})
            results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                args = block.get("input", {}) or {}
                tools_used.append(name)
                fn = _DISPATCH.get(name)
                if fn is None:
                    out = {"error": f"unknown tool {name!r}"}
                else:
                    try:
                        out = fn(self._db, **args)
                    except Exception as e:  # bad args / db error
                        out = {"error": str(e)}
                _collect_citations(out, citations)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("id"),
                    "content": json.dumps(out, default=str),
                })
            messages.append({"role": "user", "content": results})

        return {
            "enabled": True,
            "answer": "Stopped after the maximum number of tool steps without a final answer.",
            "citations": sorted(citations),
            "tools_used": tools_used,
        }


def _collect_citations(obj, into: set[str]) -> None:
    """Pull every session_id out of a tool result for the citations list."""
    if isinstance(obj, dict):
        sid = obj.get("session_id")
        if isinstance(sid, str):
            into.add(sid)
        for v in obj.values():
            _collect_citations(v, into)
    elif isinstance(obj, list):
        for v in obj:
            _collect_citations(v, into)


def build_incident_agent(db: Db, config: LLMConfig | None = None) -> IncidentAgent:
    return IncidentAgent(db, config)
