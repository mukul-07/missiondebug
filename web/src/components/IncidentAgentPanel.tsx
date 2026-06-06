/**
 * v2 incident agent — "Ask the incident history" panel.
 *
 * A question box over the LLM agent (POST /api/v2/incidents/ask). Renders the
 * grounded answer with click-through citations to the exact sessions it used.
 * Degrades to a calm "disabled" note when no LLM key is configured on the hub
 * (HR24) — never a dead/broken input.
 */

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { askAgent, getAgentStatus, type AgentAnswer } from "../api/agent";
import { Card } from "./ui/Card";

const EXAMPLES = [
  "Which robot is the biggest problem this month?",
  "Has the battery_low issue happened before, and what fixed it?",
  "Summarize the recurring incidents and their root causes.",
];

export function IncidentAgentPanel() {
  const { data: status } = useQuery({
    queryKey: ["agent-status"],
    queryFn: getAgentStatus,
    staleTime: 300_000,
  });
  const [q, setQ] = useState("");
  const ask = useMutation({ mutationFn: (question: string) => askAgent(question) });

  const enabled = status?.enabled ?? false;

  const submit = (question: string) => {
    const text = question.trim();
    if (!text) return;
    setQ(text);
    ask.mutate(text);
  };

  return (
    <Card>
      <div className="text-[10px] uppercase tracking-wide text-muted mb-2">
        Ask the incident history
      </div>

      {status && !enabled ? (
        <div className="text-xs text-muted">
          The natural-language agent is disabled. Set{" "}
          <span className="font-mono text-text/80">MD_LLM_API_KEY</span> on the hub
          to ask questions about your fleet's incidents in plain English. Summaries,
          similarity, and the dashboard work without it.
        </div>
      ) : (
        <div className="grid gap-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(q);
            }}
            className="flex items-center gap-2"
          >
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="e.g. why does warehouse-bot-03 keep stalling, and what fixed it?"
              className="flex-1 bg-bg border border-border rounded px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <button
              type="submit"
              disabled={ask.isPending || !q.trim()}
              className="px-3 py-2 rounded border border-accent text-accent text-sm hover:bg-accent/10 disabled:opacity-50"
            >
              {ask.isPending ? "Asking…" : "Ask"}
            </button>
          </form>

          {!ask.data && !ask.isPending ? (
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => submit(ex)}
                  className="text-xs text-muted border border-border rounded px-2 py-1 hover:border-accent hover:text-text"
                >
                  {ex}
                </button>
              ))}
            </div>
          ) : null}

          {ask.isPending ? (
            <div className="text-xs text-muted">Searching the incident history…</div>
          ) : null}

          {ask.isError ? (
            <div className="text-xs text-accent">
              Failed to ask: {String(ask.error)}
            </div>
          ) : null}

          {ask.data ? <Answer data={ask.data} /> : null}
        </div>
      )}
    </Card>
  );
}

function Answer({ data }: { data: AgentAnswer }) {
  if (data.error) {
    return <div className="text-xs text-accent">{data.answer}</div>;
  }
  return (
    <div className="grid gap-2 border-t border-border pt-3">
      <div className="text-sm text-text leading-relaxed whitespace-pre-wrap">
        {data.answer}
      </div>
      {data.citations.length > 0 ? (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] uppercase tracking-wide text-muted">Cited</span>
          {data.citations.map((sid) => (
            <Link
              key={sid}
              to={`/sessions/${encodeURIComponent(sid)}`}
              className="text-xs font-mono px-1.5 py-0.5 rounded bg-accent/20 text-accent hover:bg-accent/30"
            >
              {sid}
            </Link>
          ))}
        </div>
      ) : null}
      {data.tools_used && data.tools_used.length > 0 ? (
        <div className="text-[10px] text-muted/70">
          via {Array.from(new Set(data.tools_used)).join(", ")}
        </div>
      ) : null}
    </div>
  );
}
