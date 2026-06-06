"""v2 incident agent — the ROSA-pattern natural-language Q&A layer.

Drives the tool-use loop with a scripted fake model, so the loop + tool
execution + grounding/citations are verified without a real LLM key.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from missiondebug_backend.db import Db, SessionRow, now_ms
from missiondebug_backend.incident_agent import (
    IncidentAgent,
    tool_get_incident,
    tool_search_incidents,
)
from missiondebug_backend.main import build_app

_BASE = 1_700_000_000_000
_DAY = 86_400_000
_SUMMARY = (
    "Auto-triggered by rule 'battery_low' on bot-03 (subsystem: power). "
    "Captured 60s across topics: /battery_state, /cmd_vel."
)


def _seed(db: Db) -> None:
    db.upsert_session(SessionRow(
        id="SES-201", robot_id="bot-03", started_at=_BASE, ended_at=_BASE + 60_000,
        duration_ms=60_000, label="anomaly:battery_low", mcap_path="",
        mcap_size_bytes=100, topics=["/battery_state", "/cmd_vel"],
        created_at=now_ms(), subsystem="power", summary=_SUMMARY,
    ))
    db.upsert_resolution(
        session_id="SES-201", status="resolved",
        root_cause="Battery pack cell 3 degraded; replaced module",
        linked_ticket="JIRA-4471", duplicate_of=None, edited_by="op",
    )
    db.upsert_session(SessionRow(
        id="SES-203", robot_id="bot-03", started_at=_BASE + 10 * _DAY,
        ended_at=_BASE + 10 * _DAY + 60_000, duration_ms=60_000,
        label="anomaly:battery_low", mcap_path="", mcap_size_bytes=100,
        topics=["/battery_state", "/cmd_vel"], created_at=now_ms(),
        subsystem="power", summary=_SUMMARY,
    ))


class ScriptedModel:
    """Returns pre-scripted Anthropic-shaped responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, system, messages, tools):
        self.calls += 1
        return self.responses.pop(0)


def _tool_use(tid, name, inp):
    return {"stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}


def _final(text):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def test_agent_loop_executes_tools_and_cites(tmp_path: Path):
    db = Db(tmp_path / "db.sqlite3")
    _seed(db)
    model = ScriptedModel([
        _tool_use("t1", "find_similar", {"session_id": "SES-203", "k": 3}),
        _final("This battery_low on bot-03 happened before in SES-201 — resolved by "
               "replacing the cell module (JIRA-4471)."),
    ])
    agent = IncidentAgent(db, call_model=model)
    assert agent.enabled

    out = agent.ask("Has the bot-03 battery issue happened before?")
    assert out["enabled"] is True
    assert "find_similar" in out["tools_used"]
    assert "SES-201" in out["citations"]          # surfaced by the tool result
    assert "SES-201" in out["answer"]
    assert model.calls == 2                         # one tool round + one final


def test_disabled_without_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MD_LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = IncidentAgent(Db(tmp_path / "db.sqlite3"))  # no key, no injected model
    assert agent.enabled is False
    out = agent.ask("anything")
    assert out["enabled"] is False
    assert "disabled" in out["answer"].lower()


def test_tools_return_bounded_metadata(tmp_path: Path):
    db = Db(tmp_path / "db.sqlite3")
    _seed(db)
    inc = tool_get_incident(db, session_id="SES-201")
    # HR26: only incident metadata — no file paths, urls, or raw bytes.
    assert "mcap_path" not in inc and "mcap_url" not in inc
    assert inc["session_id"] == "SES-201"
    assert inc["root_cause"].startswith("Battery pack")
    found = tool_search_incidents(db, rule="battery_low")
    assert found["count"] == 2
    for item in found["incidents"]:
        assert "mcap_path" not in item and "mcap_url" not in item


def test_ask_endpoint(tmp_path: Path):
    db_path = tmp_path / "db.sqlite3"
    _seed(Db(db_path))
    agent = IncidentAgent(Db(db_path), call_model=ScriptedModel([_final("No matches.")]))
    app = build_app(tmp_path / "sessions", db_path, incident_agent=agent)
    with TestClient(app) as client:
        assert client.get("/api/v2/incidents/agent").json()["enabled"] is True
        r = client.post("/api/v2/incidents/ask", json={"question": "hello"})
        assert r.status_code == 200
        assert r.json()["answer"] == "No matches."
