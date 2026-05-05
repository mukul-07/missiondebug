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
}

export async function listSessions(): Promise<SessionSummary[]> {
  const r = await fetch("/api/sessions");
  if (!r.ok) throw new Error(`listSessions: ${r.status}`);
  const j = await r.json();
  return j.sessions;
}

export async function getSession(id: string): Promise<SessionSummary> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`getSession: ${r.status}`);
  return r.json();
}

export function mcapUrl(id: string): string {
  return `/api/sessions/${encodeURIComponent(id)}/mcap`;
}
