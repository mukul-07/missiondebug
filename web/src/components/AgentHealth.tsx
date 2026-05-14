import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  type AgentHealthRow,
  type AgentStatus,
  getFleetHealth,
} from "../api/agents";
import { EmptyState } from "./ui/EmptyState";
import { Skeleton } from "./ui/Skeleton";

/**
 * v2 P2.2 — Fleet operational health page (route: /fleet/agents).
 *
 * Answers the single most-asked fleet question: "is MissionDebug
 * actually running on all my robots right now?"
 *
 * Three sections, in fixed order — Silent (most urgent), Stale, then
 * Healthy (collapsible because it's typically the bulk). Each row
 * shows robot_id, silence duration, agent_version, subsystem.
 *
 * Refetches every 10s so the page reflects current state without a
 * page reload.
 */

const SILENCE_COLOR: Record<AgentStatus, string> = {
  healthy: "text-text",
  stale: "text-yellow-400",
  silent: "text-accent",
};

const DOT_COLOR: Record<AgentStatus, string> = {
  healthy: "bg-text/70",
  stale: "bg-yellow-400",
  silent: "bg-accent",
};

function fmtSilence(seconds: number): string {
  if (seconds < 0) return "never";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m ${seconds % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h`;
}

function fmtLastSeen(ms: number | null): string {
  if (ms === null) return "—";
  return new Date(ms).toLocaleString();
}

export function AgentHealth() {
  const [healthyExpanded, setHealthyExpanded] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["fleet-health"],
    queryFn: getFleetHealth,
    refetchInterval: 10_000,
  });

  if (isLoading) {
    return (
      <div className="p-4 grid gap-3 max-w-4xl">
        <h2 className="text-lg">Fleet health</h2>
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-4 text-accent">Error loading health: {String(error)}</div>
    );
  }
  if (!data) return null;

  if (data.total === 0) {
    return (
      <div className="p-4 grid gap-3 max-w-4xl">
        <h2 className="text-lg">Fleet health</h2>
        <EmptyState
          icon="📡"
          title="No agents have reported yet"
          description={
            <span>
              When an agent is configured with <code className="font-mono">hub.url</code>,
              it'll appear here and start sending heartbeats every 60 seconds.
            </span>
          }
        />
      </div>
    );
  }

  const silent = data.agents.filter((a) => a.status === "silent");
  const stale = data.agents.filter((a) => a.status === "stale");
  const healthy = data.agents.filter((a) => a.status === "healthy");

  const summary = `${data.healthy} of ${data.total} reporting`;
  const subtle = [
    data.stale > 0 ? `${data.stale} stale` : null,
    data.silent > 0 ? `${data.silent} silent` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="p-4 grid gap-3 max-w-4xl">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h2 className="text-lg">Fleet health</h2>
        <span className="text-sm">{summary}</span>
        {subtle ? <span className="text-sm text-muted">· {subtle}</span> : null}
        <span
          className="ml-auto text-[10px] text-muted"
          title={`healthy if heartbeat < ${data.thresholds.healthy_seconds}s ago; stale up to ${data.thresholds.stale_seconds}s; silent beyond`}
        >
          thresholds: healthy &lt; {data.thresholds.healthy_seconds}s
          · stale &lt; {data.thresholds.stale_seconds}s
        </span>
      </div>

      {silent.length > 0 ? (
        <Section title="Silent" status="silent" rows={silent} />
      ) : null}

      {stale.length > 0 ? (
        <Section title="Stale" status="stale" rows={stale} />
      ) : null}

      {healthy.length > 0 ? (
        <div className="bg-panel border border-border rounded">
          <button
            type="button"
            onClick={() => setHealthyExpanded((e) => !e)}
            className="w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:text-text"
          >
            <span
              className={`inline-block w-2 h-2 rounded-full ${DOT_COLOR.healthy}`}
            />
            <span>Healthy</span>
            <span className="text-muted text-xs">{healthy.length}</span>
            <span className="ml-auto text-muted text-xs">
              {healthyExpanded ? "▾" : "▸"}
            </span>
          </button>
          {healthyExpanded ? (
            <div className="border-t border-border">
              <Rows rows={healthy} status="healthy" />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Section({
  title,
  status,
  rows,
}: {
  title: string;
  status: AgentStatus;
  rows: AgentHealthRow[];
}) {
  return (
    <div className="bg-panel border border-border rounded">
      <div className="px-3 py-2 text-sm flex items-center gap-2 border-b border-border">
        <span
          className={`inline-block w-2 h-2 rounded-full ${DOT_COLOR[status]}`}
        />
        <span>{title}</span>
        <span className="text-muted text-xs">{rows.length}</span>
      </div>
      <Rows rows={rows} status={status} />
    </div>
  );
}

function Rows({
  rows,
  status,
}: {
  rows: AgentHealthRow[];
  status: AgentStatus;
}) {
  return (
    <ul className="divide-y divide-border">
      {rows.map((a) => (
        <li key={a.robot_id} className="px-3 py-2 grid grid-cols-12 gap-3 items-baseline">
          <Link
            to={`/?robot=${encodeURIComponent(a.robot_id)}`}
            className="col-span-3 font-mono text-sm hover:text-accent truncate"
            title={`See sessions for ${a.robot_id}`}
          >
            {a.robot_id}
          </Link>
          <div className={`col-span-2 text-xs font-mono ${SILENCE_COLOR[status]}`}>
            {fmtSilence(a.silence_seconds)}
          </div>
          <div className="col-span-3 text-xs text-muted truncate" title={fmtLastSeen(a.last_heartbeat)}>
            {a.last_heartbeat === null
              ? "never heartbeated"
              : `last ${fmtLastSeen(a.last_heartbeat)}`}
          </div>
          <div className="col-span-2 text-xs font-mono text-muted truncate">
            {a.subsystem ?? "—"}
          </div>
          <div className="col-span-2 text-xs font-mono text-muted truncate" title={a.agent_version ?? ""}>
            {a.agent_version ?? "—"}
          </div>
        </li>
      ))}
    </ul>
  );
}
