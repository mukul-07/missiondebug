/**
 * v2 incident agent — natural-language Q&A over the incident history.
 *
 * Opt-in on the hub (needs an LLM key). `getAgentStatus` lets the UI render
 * a graceful "disabled" state instead of a dead input box.
 */

export interface AgentAnswer {
  enabled: boolean;
  answer: string;
  citations: string[];
  tools_used: string[];
  error?: boolean;
}

export async function getAgentStatus(): Promise<{ enabled: boolean }> {
  const r = await fetch("/api/v2/incidents/agent");
  if (!r.ok) throw new Error(`getAgentStatus: ${r.status}`);
  return r.json();
}

export async function askAgent(question: string): Promise<AgentAnswer> {
  const r = await fetch("/api/v2/incidents/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!r.ok) throw new Error(`askAgent: ${r.status}`);
  return r.json();
}
