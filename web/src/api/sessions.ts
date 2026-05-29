export interface SessionSummary {
  id: string;
  robot_id: string;
  started_at: number; // unix ms
  ended_at: number;
  duration_ms: number;
  label: string | null;
  mcap_size_bytes: number;
  topics: string[];
  created_at: number;
  annotation_count?: number;
  // v2 (fleet) additions — optional for v1.5 single-robot sessions.
  subsystem?: string | null;
  source?: "agent" | "local";
  // v2 P3.5.1: structured summary generated agent-side at save time.
  // Null on sessions ingested before the summarizer existed (no backfill
  // in this phase — those older rows stay summary-less in the list).
  summary?: string | null;
}

export interface SessionListResult {
  sessions: SessionSummary[];
  robots: string[];
}

export interface ListSessionsParams {
  robotId?: string | null;
  subsystem?: string | null;
}

export async function listSessions(
  params?: ListSessionsParams,
): Promise<SessionListResult> {
  const qs = new URLSearchParams();
  if (params?.robotId) qs.set("robot_id", params.robotId);
  if (params?.subsystem) qs.set("subsystem", params.subsystem);
  const query = qs.toString();
  const url = query ? `/api/sessions?${query}` : "/api/sessions";
  const r = await fetch(url);
  if (!r.ok) throw new Error(`listSessions: ${r.status}`);
  const j = await r.json();
  return { sessions: j.sessions, robots: j.robots ?? [] };
}

export async function getSession(id: string): Promise<SessionSummary> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`getSession: ${r.status}`);
  return r.json();
}

export function mcapUrl(id: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/mcap`;
}
