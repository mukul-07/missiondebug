import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { listSessions, type SessionSummary } from "../api/sessions";
import { Card } from "./ui/Card";
import { EmptyState } from "./ui/EmptyState";
import { SkeletonSessionList } from "./ui/Skeleton";

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

  const { data, isLoading, error } = useQuery({
    queryKey: ["sessions", robotFilter],
    queryFn: () => listSessions(robotFilter),
    refetchInterval: 5_000,
  });

  if (isLoading) {
    return (
      <div className="p-4 grid gap-2 max-w-3xl">
        <div className="text-lg">Sessions</div>
        <SkeletonSessionList count={5} />
      </div>
    );
  }
  if (error) return <div className="p-4 text-accent">Error: {String(error)}</div>;

  const sessions = data?.sessions ?? [];
  const robots = data?.robots ?? [];

  const setRobot = (r: string | null) => {
    if (r) params.set("robot", r);
    else params.delete("robot");
    setParams(params, { replace: true });
  };

  const totalRobots = robots.length;
  const summary = sessions.length === 0
    ? null
    : `${sessions.length} session${sessions.length === 1 ? "" : "s"}` +
      (robotFilter
        ? ` for ${robotFilter}`
        : totalRobots > 0
        ? ` across ${totalRobots} robot${totalRobots === 1 ? "" : "s"}`
        : "");

  return (
    <div className="p-4 grid gap-2 max-w-3xl">
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <h2 className="text-lg">Sessions</h2>
          {summary ? <span className="text-xs text-muted">{summary}</span> : null}
        </div>
        {robots.length > 0 && (
          <div className="flex gap-1 text-xs">
            <button
              onClick={() => setRobot(null)}
              className={`px-2 py-0.5 rounded border ${
                !robotFilter
                  ? "bg-accent/20 text-accent border-accent"
                  : "border-border text-muted hover:text-text"
              }`}
            >
              all
            </button>
            {robots.map((r) => (
              <button
                key={r}
                onClick={() => setRobot(r)}
                className={`px-2 py-0.5 rounded border font-mono ${
                  robotFilter === r
                    ? "bg-accent/20 text-accent border-accent"
                    : "border-border text-muted hover:text-text"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        )}
      </div>

      {sessions.length === 0 ? (
        <EmptyState
          icon="📼"
          title={
            robotFilter
              ? `No sessions for ${robotFilter}`
              : "No sessions yet"
          }
          description={
            robotFilter
              ? "Try clearing the robot filter, or trigger an anomaly on this robot."
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
}
