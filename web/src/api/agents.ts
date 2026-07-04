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
  if (r.status === 404) {
    // Pre-v2 backend (endpoint doesn't exist) — return a synthetic empty
    // shape so the page can render its zero-state. ONLY for 404: any other
    // failure (500, auth 401, network) must throw, or a transient hub error
    // masquerades as "no agents have reported yet" on the exact page whose
    // job is to answer "is MissionDebug running on my robots?".
    return {
      healthy: 0, stale: 0, silent: 0, total: 0,
      agents: [],
      thresholds: { healthy_seconds: 120, stale_seconds: 300 },
    };
  }
  if (!r.ok) throw new Error(`getFleetHealth: ${r.status}`);
  return r.json();
}

/**
 * v2 — live ROS topic discovery on one robot, proxied through the hub
 * (GET /api/v1/agents/{robot_id}/topics; needs agent >= 0.7.0 on the robot).
 */
export interface AgentTopic {
  name: string;
  type: string;
  resolvable: boolean;   // false = message package not built on the robot
  category: string;      // control | state | safety | perception | transform | plan | debug | other
  recommended: boolean;  // whether the capture checkbox should start checked
  reason: string | null; // plain-language why, null for "other"
  large: boolean;        // high-rate/large type (Image, PointCloud2, ...)
  publishers: number | null; // 0 = advertised but silent; null = unknown
}

export interface AgentTopics {
  robot_id: string;
  agent_version: string | null;
  settled: boolean;      // false = partial DDS scan, list may be incomplete
  topics: AgentTopic[];
  last_capture_topics: string[] | null; // topics in this robot's most recent session
  last_capture_session_id?: string | null; // that session's id (hub >= this build)
}

/**
 * Error carrying the HTTP status so the panel can render state-specific
 * guidance (409 no reachable URL, 426 agent too old, 502 unreachable).
 */
export class AgentTopicsError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`getAgentTopics: ${status}${detail ? ` ${detail}` : ""}`);
    this.status = status;
    this.detail = detail;
  }
}

export async function getAgentTopics(robotId: string): Promise<AgentTopics> {
  const r = await fetch(`/api/v1/agents/${encodeURIComponent(robotId)}/topics`);
  if (!r.ok) {
    let detail = "";
    try {
      detail = (await r.json()).detail ?? "";
    } catch {
      // non-JSON error body — keep the status only
    }
    throw new AgentTopicsError(r.status, detail);
  }
  return r.json();
}
