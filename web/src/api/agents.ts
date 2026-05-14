/**
 * v2 fleet API: list reporting agents from the hub.
 * Used by the SessionList's FilterRail to render the
 * Fleet → Subsystem → Robot hierarchy.
 */

export interface AgentInfo {
  robot_id: string;
  first_seen: number;
  last_heartbeat: number | null;
  agent_version: string | null;
  agent_url: string | null;
  subsystem: string | null;
}

export async function listAgents(): Promise<AgentInfo[]> {
  const r = await fetch("/api/v1/agents");
  if (!r.ok) {
    // In v1.5 single-robot mode the endpoint may exist but return empty.
    // In a pre-v2 build it may 404 — degrade gracefully.
    return [];
  }
  const j = await r.json();
  return j.agents ?? [];
}
