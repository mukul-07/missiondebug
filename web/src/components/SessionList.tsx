import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listSessions, type SessionSummary } from "../api/sessions";
import { Card } from "./ui/Card";

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
  const { data, isLoading, error } = useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
    refetchInterval: 5_000,
  });

  if (isLoading) return <div className="p-4 text-muted">Loading…</div>;
  if (error) return <div className="p-4 text-accent">Error: {String(error)}</div>;
  if (!data || data.length === 0)
    return (
      <div className="p-6 text-muted">
        No sessions yet. Start the agent and POST to <code>/sessions/save</code> or
        wait for an anomaly.
      </div>
    );

  return (
    <div className="p-4 grid gap-2 max-w-3xl">
      <h2 className="text-lg">Sessions</h2>
      {data.map((s: SessionSummary) => (
        <Link key={s.id} to={`/sessions/${encodeURIComponent(s.id)}`}>
          <Card className="hover:border-accent cursor-pointer">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-mono text-sm">{s.id}</div>
                <div className="text-xs text-muted">
                  {s.robot_id} · {relativeTime(s.started_at)} · {fmtDuration(s.duration_ms)}
                  {s.label ? (
                    <span className="ml-2 px-2 py-0.5 rounded bg-accent/20 text-accent">
                      {s.label}
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="text-xs text-muted">{s.topics.length} topics</div>
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}
