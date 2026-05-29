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

/**
 * v2 P3.5.2 — "Has this happened before?" similarity result.
 *
 * Top K past sessions whose structured summaries lexically resemble
 * the query session's summary. Strictly past (older than the query
 * session); the query session and any later session are never
 * returned. Zero-score candidates are filtered server-side.
 */
export interface SimilarIncident {
  session_id: string;
  /** Cosine similarity in [0, 1]. Server-rounded to 4 decimals. */
  score: number;
  label: string | null;
  robot_id: string;
  subsystem: string | null;
  started_at: number;
  summary: string | null;
}

export interface SimilarResponse {
  similar: SimilarIncident[];
  /** Present (and `similar` empty) when the query session itself has no
   * summary — typically older sessions ingested before the v2 P3.5.1
   * summarizer existed. */
  reason?: string;
}

export async function getSimilarSessions(
  id: string,
  k = 3,
): Promise<SimilarResponse> {
  const r = await fetch(
    `/api/v2/sessions/${encodeURIComponent(id)}/similar?k=${k}`,
  );
  if (!r.ok) throw new Error(`getSimilarSessions: ${r.status}`);
  return r.json();
}
