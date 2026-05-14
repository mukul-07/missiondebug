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

/**
 * v2 P2.2 — fleet operational health.
 *
 * Returned by GET /api/v1/agents/health. Includes aggregate counts +
 * a per-agent list sorted "most urgent first" (silent → stale →
 * healthy, most-silent first within each bucket).
 */
export type AgentStatus = "healthy" | "stale" | "silent";

export interface AgentHealthRow {
  robot_id: string;
  status: AgentStatus;
  last_heartbeat: number | null;
  silence_seconds: number;       // -1 when never reported
  first_seen: number;
  agent_version: string | null;
  agent_url: string | null;
  subsystem: string | null;
}

export interface FleetHealth {
  healthy: number;
  stale: number;
  silent: number;
  total: number;
  agents: AgentHealthRow[];
  thresholds: { healthy_seconds: number; stale_seconds: number };
}

export async function getFleetHealth(): Promise<FleetHealth> {
  const r = await fetch("/api/v1/agents/health");
  if (!r.ok) {
    // Pre-v2 backend or empty hub — return a synthetic empty shape so
    // the page can render its zero-state.
    return {
      healthy: 0, stale: 0, silent: 0, total: 0,
      agents: [],
      thresholds: { healthy_seconds: 120, stale_seconds: 300 },
    };
  }
  return r.json();
}
