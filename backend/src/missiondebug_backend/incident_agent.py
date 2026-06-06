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

_ANTHROPIC_VERSION = "2023-06-01"
_MAX_TOOL_ROUNDS = 6
_MAX_TOKENS = 1024

# Provider-specific defaults. OpenAI-compatible covers OpenAI itself plus
# local servers (Ollama, vLLM, LM Studio) — so it doubles as the air-gap path.
_DEFAULT_MODELS = {"anthropic": "claude-sonnet-4-6", "openai": "gpt-4o-mini"}
_DEFAULT_BASE_URLS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
}

_SYSTEM_PROMPT = (
    "You are MissionDebug's fleet incident-history assistant. Answer the "
    "operator's question about their robot fleet's past incidents using ONLY "
    "the provided tools — never invent incidents, robots, rules, or fixes. "
    "Cite the specific session ids you relied on (e.g. SES-203). Be concise "
    "and practical: if a similar incident was resolved, surface its root "
    "cause and ticket. If the tools don't have the answer, say so plainly.\n"
    "Formatting: use light markdown — short **bold** labels and bullet lists, "
    "kept brief. Write session ids and ticket ids as PLAIN TEXT (e.g. SES-203, "
    "JIRA-4471) — never wrap them in markdown links or invent URLs."
)


@dataclass
class LLMConfig:
    provider: str = "anthropic"  # "anthropic" | "openai" (openai = OpenAI-compatible)
    api_key: str | None = None
    model: str = ""
    base_url: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> LLMConfig:
        api_key = (
            os.environ.get("MD_LLM_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or None
        )
        # Explicit MD_LLM_PROVIDER wins; otherwise sniff the key prefix
        # ("sk-ant-" = Anthropic, any other "sk-" = OpenAI-compatible).
        provider = os.environ.get("MD_LLM_PROVIDER", "").strip().lower()
        if provider not in ("anthropic", "openai"):
            if api_key and api_key.startswith("sk-ant-"):
                provider = "anthropic"
            elif api_key and api_key.startswith("sk-"):
                provider = "openai"
            else:
                provider = "anthropic"
        model = (
            os.environ.get("MD_LLM_MODEL", "").strip() or _DEFAULT_MODELS[provider]
        )
        base_url = (
            os.environ.get("MD_LLM_BASE_URL", "").strip() or _DEFAULT_BASE_URLS[provider]
        ).rstrip("/")
        return cls(provider=provider, api_key=api_key, model=model, base_url=base_url)


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
    """Dispatch to the configured provider. The agent loop is Anthropic-shaped;
    the OpenAI adapter translates at its boundary so the loop stays unchanged.
    `base_url` is configurable so either path works against a local model."""
    if config.provider == "openai":
        return _openai_call(config, system, messages, tools)
    return _anthropic_call(config, system, messages, tools)


def _anthropic_call(config: LLMConfig, system: str, messages: list, tools: list) -> dict:
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


def _openai_call(config: LLMConfig, system: str, messages: list, tools: list) -> dict:
    import httpx

    headers = {
        "authorization": f"Bearer {config.api_key or ''}",
        "content-type": "application/json",
        **config.extra_headers,
    }
    body = {
        "model": config.model,
        "max_tokens": _MAX_TOKENS,
        "messages": _to_openai_messages(system, messages),
        "tools": _to_openai_tools(tools),
    }
    resp = httpx.post(
        config.base_url + "/v1/chat/completions",
        headers=headers,
        json=body,
        timeout=60.0,
    )
    resp.raise_for_status()
    return _from_openai_response(resp.json())


# ---- OpenAI <-> Anthropic translation (keeps the loop provider-agnostic) ----

def _to_openai_tools(tools: list) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _to_openai_messages(system: str, messages: list) -> list:
    out: list = [{"role": "system", "content": system}]
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(b["text"] for b in content if b.get("type") == "text")
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input", {})),
                    },
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            msg: dict = {"role": "assistant", "content": text or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        else:  # user turn carrying tool_result (and/or text) blocks
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": b["content"],
                    })
                elif b.get("type") == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


def _from_openai_response(resp: dict) -> dict:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    tool_calls = msg.get("tool_calls") or []
    if tool_calls:
        content: list = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            content.append({
                "type": "tool_use",
                "id": tc.get("id"),
                "name": fn.get("name"),
                "input": args,
            })
        return {"stop_reason": "tool_use", "content": content}
    return {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": msg.get("content") or ""}],
    }


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

    def status(self) -> dict:
        """Whether the agent is on, and (when on) which model answers. Lets
        the UI show "powered by <model>" — the visible difference between a
        managed deployment (MissionDebug-hosted model) and a BYO-key one.
        Provider/model are omitted when disabled so nothing config-shaped
        leaks from an un-provisioned hub."""
        if not self.enabled:
            return {"enabled": False, "provider": None, "model": None}
        return {
            "enabled": True,
            "provider": self._config.provider,
            "model": self._config.model or None,
        }

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
