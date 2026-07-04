import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  type AgentHealthRow,
  type AgentStatus,
  getFleetHealth,
} from "../api/agents";
import { AgentTopicsPanel } from "./AgentTopicsPanel";
import { EmptyState } from "./ui/EmptyState";
import { LicenseBadge } from "./LicenseBadge";
import { Skeleton } from "./ui/Skeleton";

/**
 * v2 P2.2 — Fleet operational health page (route: /fleet/agents).
 *
 * Answers the single most-asked fleet question: "is MissionDebug
 * actually running on all my robots right now?"
 *
 * Three sections, in fixed order — Silent (most urgent), Stale, then
 * Healthy (collapsible because it's typically the bulk). Each row
 * shows robot_id, silence duration, agent_version, subsystem, and
 * expands to live ROS topic discovery on that robot (AgentTopicsPanel,
 * agent >= 0.7.0).
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

/** Badge when the agent's own heartbeat says configured topics are broken:
 * red for won't-capture (unresolvable/missing), amber for silent ghosts. */
function TopicsHealthBadge({ a }: { a: AgentHealthRow }) {
  const th = a.topics_health;
  if (!th) return null;
  const broken = th.unresolvable.length + th.missing.length;
  const quiet = th.silent.length;
  if (broken + quiet === 0) return null;
  const detail = [
    th.unresolvable.length ? `type not built: ${th.unresolvable.join(", ")}` : "",
    th.missing.length ? `missing from graph: ${th.missing.join(", ")}` : "",
    th.silent.length ? `no publishers: ${th.silent.join(", ")}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const cls = broken > 0
    ? "bg-red-500/20 text-red-400 border-red-500/40"
    : "bg-yellow-500/20 text-yellow-400 border-yellow-500/40";
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded border font-mono whitespace-nowrap shrink-0 ${cls}`}
      title={`Capture config vs live graph — ${detail}`}
    >
      ⚠ {broken + quiet} topic{broken + quiet === 1 ? "" : "s"}
    </span>
  );
}

export function AgentHealth() {
  const [healthyExpanded, setHealthyExpanded] = useState(false);
  // Which robots have their topics panel open. Lives here (not in the row)
  // so an open panel survives its row moving between the Silent/Stale/
  // Healthy sections when the robot's status flips on a refetch.
  const [openRobots, setOpenRobots] = useState<Set<string>>(new Set());
  const toggleRobot = (robotId: string) =>
    setOpenRobots((prev) => {
      const next = new Set(prev);
      if (next.has(robotId)) next.delete(robotId);
      else next.add(robotId);
      return next;
    });

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["fleet-health"],
    queryFn: getFleetHealth,
    refetchInterval: 10_000,
    retry: 1, // fail fast — first feedback in ~2s, the interval keeps retrying anyway
  });

  // Latch the last error: interval refetches of a no-data query cycle it
  // back through pending (error momentarily null), which would flap the UI
  // between the error card and the skeleton every 10s. Once we've errored
  // with no data, stay on the card until data actually arrives.
  const lastErrorRef = useRef<unknown>(null);
  if (error) lastErrorRef.current = error;
  const effectiveError = error ?? lastErrorRef.current;

  // Error with no data at all (first load failed): calm card, not a raw
  // exception. When a *refetch* fails, data is still set — we render the
  // last-good list below with a warning strip instead of blanking the page.
  if (!data && effectiveError) {
    return (
      <div className="p-4 grid gap-3 max-w-4xl">
        <h2 className="text-lg">Fleet health</h2>
        <div className="bg-panel border border-border rounded p-4 grid gap-2">
          <div className="text-sm">Couldn't reach the hub.</div>
          <div className="text-xs text-muted">
            The fleet is probably fine — this page just can't ask the hub
            about it right now. Retrying automatically every 10s.
          </div>
          <div>
            <button
              type="button"
              onClick={() => refetch()}
              className="px-3 py-1.5 rounded border border-border text-sm hover:border-accent hover:text-accent"
            >
              Retry now
            </button>
          </div>
        </div>
      </div>
    );
  }
  if (isLoading || !data) {
    return (
      <div className="p-4 grid gap-3 max-w-4xl">
        <h2 className="text-lg">Fleet health</h2>
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

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
        <span className="ml-auto"><LicenseBadge /></span>
        <span
          className="text-[10px] text-muted"
          title={`healthy if heartbeat < ${data.thresholds.healthy_seconds}s ago; stale up to ${data.thresholds.stale_seconds}s; silent beyond`}
        >
          thresholds: healthy &lt; {data.thresholds.healthy_seconds}s
          · stale &lt; {data.thresholds.stale_seconds}s
        </span>
      </div>

      {error ? (
        <div className="flex items-center gap-3 flex-wrap text-xs border border-yellow-500/40 bg-yellow-500/10 text-yellow-400 rounded px-3 py-2">
          <span>⚠ Couldn't reach the hub — showing the last known state.</span>
          <button
            type="button"
            onClick={() => refetch()}
            className="ml-auto px-2 py-1 rounded border border-yellow-500/40 hover:border-yellow-400"
          >
            Retry
          </button>
        </div>
      ) : null}

      {silent.length > 0 ? (
        <Section title="Silent" status="silent" rows={silent}
          openRobots={openRobots} onToggle={toggleRobot} />
      ) : null}

      {stale.length > 0 ? (
        <Section title="Stale" status="stale" rows={stale}
          openRobots={openRobots} onToggle={toggleRobot} />
      ) : null}

      {healthy.length > 0 ? (
        <div className="bg-panel border border-border rounded">
          <button
            type="button"
            onClick={() => setHealthyExpanded((e) => !e)}
            aria-expanded={healthyExpanded}
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
              <Rows rows={healthy} status="healthy"
                openRobots={openRobots} onToggle={toggleRobot} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// Shared responsive column spans: below sm the low-value columns (last
// seen, subsystem) hide — their info survives in title attrs and the
// expanded panel — and robot_id takes the freed space.
const COL = {
  robot: "col-span-6 sm:col-span-3",
  silence: "col-span-3 sm:col-span-2",
  lastSeen: "hidden sm:block sm:col-span-2",
  subsystem: "hidden sm:block sm:col-span-2",
  version: "col-span-2 sm:col-span-2",
  toggle: "col-span-1",
};

type RowsProps = {
  rows: AgentHealthRow[];
  status: AgentStatus;
  openRobots: Set<string>;
  onToggle: (robotId: string) => void;
};

function Section({
  title,
  status,
  rows,
  openRobots,
  onToggle,
}: RowsProps & { title: string }) {
  return (
    <div className="bg-panel border border-border rounded">
      <div className="px-3 py-2 text-sm flex items-center gap-2 border-b border-border">
        <span
          className={`inline-block w-2 h-2 rounded-full ${DOT_COLOR[status]}`}
        />
        <span>{title}</span>
        <span className="text-muted text-xs">{rows.length}</span>
      </div>
      <Rows rows={rows} status={status} openRobots={openRobots} onToggle={onToggle} />
    </div>
  );
}

function Rows({ rows, status, openRobots, onToggle }: RowsProps) {
  const label = "text-[10px] uppercase tracking-wide text-muted";
  return (
    <>
      <div className="px-3 pt-2 pb-1 grid grid-cols-12 gap-3 border-b border-border">
        <span className={`${COL.robot} ${label}`}>robot</span>
        <span className={`${COL.silence} ${label}`}>silence</span>
        <span className={`${COL.lastSeen} ${label}`}>last seen</span>
        <span className={`${COL.subsystem} ${label}`}>subsystem</span>
        <span className={`${COL.version} ${label}`}>agent</span>
        <span className={COL.toggle} />
      </div>
      <ul className="divide-y divide-border">
        {rows.map((a) => (
          <Row
            key={a.robot_id}
            a={a}
            status={status}
            open={openRobots.has(a.robot_id)}
            onToggle={() => onToggle(a.robot_id)}
          />
        ))}
      </ul>
    </>
  );
}

function Row({
  a,
  status,
  open,
  onToggle,
}: {
  a: AgentHealthRow;
  status: AgentStatus;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <li className="px-3 py-2">
      <div
        className="grid grid-cols-12 gap-3 items-baseline cursor-pointer"
        onClick={onToggle}
        title={open ? undefined : "Show live ROS topics on this robot"}
      >
        <div className={`${COL.robot} flex items-baseline gap-1.5 min-w-0`}>
          <Link
            to={`/?robot=${encodeURIComponent(a.robot_id)}`}
            className="font-mono text-sm hover:text-accent truncate"
            title={`See sessions for ${a.robot_id}`}
            onClick={(e) => e.stopPropagation()}
          >
            {a.robot_id}
          </Link>
          <TopicsHealthBadge a={a} />
        </div>
        <div className={`${COL.silence} text-xs font-mono ${SILENCE_COLOR[status]}`}>
          {fmtSilence(a.silence_seconds)}
        </div>
        <div className={`${COL.lastSeen} text-xs text-muted truncate`} title={fmtLastSeen(a.last_heartbeat)}>
          {a.last_heartbeat === null
            ? "never heartbeated"
            : `last ${fmtLastSeen(a.last_heartbeat)}`}
        </div>
        <div className={`${COL.subsystem} text-xs font-mono text-muted truncate`}>
          {a.subsystem ?? "—"}
        </div>
        <div className={`${COL.version} text-xs font-mono text-muted truncate`} title={a.agent_version ?? ""}>
          {a.agent_version ?? "—"}
        </div>
        <button
          type="button"
          className={`${COL.toggle} text-[10px] text-muted hover:text-text text-right whitespace-nowrap`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} topics on ${a.robot_id}`}
        >
          {open ? "▾" : "▸"} topics
        </button>
      </div>
      {open ? (
        <div className="border-t border-border mt-2 pt-2">
          <AgentTopicsPanel robotId={a.robot_id} agentVersion={a.agent_version} />
        </div>
      ) : null}
    </li>
  );
}
