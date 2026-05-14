import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { listAgents } from "../api/agents";
import { listSessions, type SessionSummary } from "../api/sessions";
import { Card } from "./ui/Card";
import { EmptyState } from "./ui/EmptyState";
import { SkeletonSessionList } from "./ui/Skeleton";
import { FilterRail, useRailGroups } from "./FilterRail";

function relativeTime(ms: number): string {
  const d = Date.now() - ms;
  if (d < 60_000) return `${Math.floor(d / 1000)}s ago`;
  if (d < 3_600_000) return `${Math.floor(d / 60_000)}m ago`;
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h ago`;
  return `${Math.floor(d / 86_400_000)}d ago`;
}

function fmtDuration(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

export function SessionList() {
  const [params, setParams] = useSearchParams();
  const robotFilter = params.get("robot");
  const subsystemFilter = params.get("subsystem");

  const { data, isLoading, error } = useQuery({
    queryKey: ["sessions", robotFilter, subsystemFilter],
    queryFn: () =>
      listSessions({ robotId: robotFilter, subsystem: subsystemFilter }),
    refetchInterval: 5_000,
  });

  // Agents are used by the FilterRail to render the
  // Fleet → Subsystem → Robot tree. Refetch less aggressively than
  // sessions — the roster rarely changes mid-incident.
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: listAgents,
    refetchInterval: 30_000,
  });

  // Compute groups before any early return. Rules of Hooks: hook
  // calls must run unconditionally in the same order every render.
  // Calling useRailGroups after `if (isLoading) return …` triggers
  // React error #310 the first time `data` resolves.
  const sessions = data?.sessions ?? [];
  const robots = data?.robots ?? [];
  const groups = useRailGroups(agents, sessions, robots);
  const showRail = groups !== null;

  if (isLoading) {
    return (
      <div className="p-4 grid gap-2 max-w-3xl">
        <div className="text-lg">Sessions</div>
        <SkeletonSessionList count={5} />
      </div>
    );
  }
  if (error) return <div className="p-4 text-accent">Error: {String(error)}</div>;

  const totalRobots = robots.length;
  const summary = sessions.length === 0
    ? null
    : `${sessions.length} session${sessions.length === 1 ? "" : "s"}` +
      (robotFilter
        ? ` for ${robotFilter}`
        : subsystemFilter
        ? ` in ${subsystemFilter}`
        : totalRobots > 0
        ? ` across ${totalRobots} robot${totalRobots === 1 ? "" : "s"}`
        : "");

  const setSubsystem = (s: string | null) => {
    if (s) params.set("subsystem", s);
    else params.delete("subsystem");
    setParams(params, { replace: true });
  };
  const setRobot = (r: string | null) => {
    if (r) params.set("robot", r);
    else params.delete("robot");
    setParams(params, { replace: true });
  };

  const sessionsBody = (
    <div className="flex-1 grid gap-2">
      {sessions.length === 0 ? (
        <EmptyState
          icon="📼"
          title={
            robotFilter
              ? `No sessions for ${robotFilter}`
              : subsystemFilter
              ? `No sessions in ${subsystemFilter}`
              : "No sessions yet"
          }
          description={
            robotFilter || subsystemFilter
              ? "Try clearing the filter, or trigger an anomaly on this robot."
              : "Start the agent and trigger an anomaly — or POST to /sessions/save manually."
          }
        />
      ) : (
        sessions.map((s: SessionSummary) => (
          <Link key={s.id} to={`/sessions/${encodeURIComponent(s.id)}`}>
            <Card className="hover:border-accent cursor-pointer">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-mono text-sm">{s.id}</div>
                  <div className="text-xs text-muted">
                    <span className="px-1.5 py-0.5 mr-2 rounded bg-bg border border-border font-mono">
                      {s.robot_id}
                    </span>
                    {s.subsystem ? (
                      <span
                        title={`subsystem: ${s.subsystem}`}
                        className="mr-2 px-1.5 py-0.5 rounded bg-bg border border-border font-mono"
                      >
                        {s.subsystem}
                      </span>
                    ) : null}
                    {relativeTime(s.started_at)} · {fmtDuration(s.duration_ms)}
                    {s.label ? (
                      <span className="ml-2 px-2 py-0.5 rounded bg-accent/20 text-accent">
                        {s.label}
                      </span>
                    ) : null}
                    {s.source === "agent" ? (
                      <span
                        title="ingested from a remote agent"
                        className="ml-2 text-[10px] uppercase tracking-wide text-muted opacity-70"
                      >
                        via agent
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="text-xs text-muted flex items-center gap-2">
                  {s.annotation_count && s.annotation_count > 0 ? (
                    <span className="px-1.5 py-0.5 rounded bg-bg border border-border">
                      📝 {s.annotation_count}
                    </span>
                  ) : null}
                  {s.topics.length} topics
                </div>
              </div>
            </Card>
          </Link>
        ))
      )}
    </div>
  );

  return (
    <div className="p-4">
      <div className="flex items-baseline gap-2 mb-3">
        <h2 className="text-lg">Sessions</h2>
        {summary ? <span className="text-xs text-muted">{summary}</span> : null}
      </div>

      {showRail ? (
        <div className="flex gap-4 max-w-5xl">
          <FilterRail
            agents={agents}
            sessions={sessions}
            robotIds={robots}
            selectedSubsystem={subsystemFilter}
            selectedRobot={robotFilter}
            onSelectSubsystem={setSubsystem}
            onSelectRobot={setRobot}
          />
          {sessionsBody}
        </div>
      ) : (
        <div className="max-w-3xl">{sessionsBody}</div>
      )}
    </div>
  );
}
