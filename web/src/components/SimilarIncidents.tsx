/**
 * v2 P3.5.5 — "Has this happened before?" panel.
 *
 * Renders the top K past sessions whose structured summaries lexically
 * resemble this session's summary. Powered by the v2 P3.5.2 similarity
 * endpoint (`GET /api/v2/sessions/{id}/similar`).
 *
 * Empty / loading / no-summary states are first-class — this card has
 * to land cleanly on every session in the fleet, including older ones
 * ingested before the P3.5.1 summarizer existed. Failing silently on
 * those would erode trust; we explain instead.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  getResolution,
  getSimilarSessions,
  putResolution,
  type Resolution,
  type SimilarIncident,
} from "../api/sessions";
import { Card } from "./ui/Card";
import { ResolutionBadge } from "./ResolutionPanel";
import { Skeleton } from "./ui/Skeleton";

/** Similarity above which the top match is offered as a one-click duplicate.
 * TF-IDF over structured summaries scores same-rule same-robot repeats well
 * above this; unrelated incidents land far below. */
const SUGGEST_THRESHOLD = 0.55;

interface Props {
  sessionId: string;
  /** Whether the parent already knows the query session has a summary.
   * Used to short-circuit the fetch — saves a network round-trip and a
   * confusing "no summary on this session" empty state on older sessions. */
  hasSummary: boolean;
}

function relativeTime(ms: number): string {
  const d = Date.now() - ms;
  if (d < 60_000) return `${Math.floor(d / 1000)}s ago`;
  if (d < 3_600_000) return `${Math.floor(d / 60_000)}m ago`;
  if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h ago`;
  return `${Math.floor(d / 86_400_000)}d ago`;
}

function scorePct(score: number): string {
  return `${Math.round(score * 100)}%`;
}

export function SimilarIncidents({ sessionId, hasSummary }: Props) {
  const enabled = !!sessionId && hasSummary;
  const { data, isLoading, error } = useQuery({
    queryKey: ["similar", sessionId],
    queryFn: () => getSimilarSessions(sessionId, 3),
    enabled,
    // Similarity is derived from the corpus, which only grows. Cache
    // aggressively — refetching on every focus would burn cycles for
    // no behavior change.
    staleTime: 60_000,
  });

  // Older sessions ingested before P3.5.1 have no summary. The endpoint
  // would return an empty array with a `reason`, but we short-circuit
  // here so we can give a clearer message (and skip the fetch).
  if (!hasSummary) {
    return (
      <Card className="bg-bg/50">
        <Header />
        <div className="text-xs text-muted">
          This session has no structured summary, so similarity search is
          unavailable. Sessions captured by an agent on v2&nbsp;P3.5.1 or
          later get one automatically.
        </div>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <Card className="bg-bg/50">
        <Header />
        <Skeleton className="h-4 w-3/4 mb-2" />
        <Skeleton className="h-4 w-2/3 mb-2" />
        <Skeleton className="h-4 w-1/2" />
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="bg-bg/50">
        <Header />
        <div className="text-xs text-accent">
          Failed to load similarity results: {String(error)}
        </div>
      </Card>
    );
  }

  const matches = data?.similar ?? [];

  if (matches.length === 0) {
    return (
      <Card className="bg-bg/50">
        <Header />
        <div className="text-xs text-muted">
          No past sessions in this hub's corpus look like this one. As more
          incidents are captured, related ones will surface here automatically.
        </div>
      </Card>
    );
  }

  return (
    <Card className="bg-bg/50">
      <Header count={matches.length} />
      <DuplicateSuggestion sessionId={sessionId} top={matches[0]} />
      <div className="grid gap-2">
        {matches.map((m) => (
          <SimilarRow key={m.session_id} m={m} />
        ))}
      </div>
    </Card>
  );
}

/**
 * One-click triage for the recurring half of incidents: when the top match
 * is strong and this session hasn't been resolved yet, offer "mark as
 * duplicate" right here. Marking feeds the recurrence KPI (explicit
 * operator-confirmed duplicates only — the suggestion never self-applies).
 */
function DuplicateSuggestion({
  sessionId,
  top,
}: {
  sessionId: string;
  top: SimilarIncident;
}) {
  const qc = useQueryClient();
  const { data: current } = useQuery({
    queryKey: ["resolution", sessionId],
    queryFn: () => getResolution(sessionId),
    staleTime: 30_000,
  });

  const mark = useMutation({
    mutationFn: () =>
      putResolution(sessionId, {
        status: "duplicate",
        duplicate_of: top.session_id,
        // Preserve anything the operator already wrote.
        root_cause: current?.root_cause ?? null,
        linked_ticket: current?.linked_ticket ?? null,
      }),
    onSuccess: (saved: Resolution) => {
      qc.setQueryData(["resolution", sessionId], saved);
      qc.invalidateQueries({ queryKey: ["fleet-incident-stats"] });
      qc.invalidateQueries({ queryKey: ["similar", sessionId] });
    },
  });

  const undecided =
    !current || current.status === "open" || current.status === "investigating";
  if (top.score < SUGGEST_THRESHOLD || !undecided) return null;

  return (
    <div className="flex items-center gap-2 flex-wrap text-xs border border-accent/40 bg-accent/10 rounded px-2.5 py-2 mb-2">
      <span>
        ⚡ Looks like a repeat of{" "}
        <Link
          to={`/sessions/${encodeURIComponent(top.session_id)}`}
          className="font-mono text-accent hover:underline"
        >
          {top.session_id}
        </Link>{" "}
        ({scorePct(top.score)} similar)
      </span>
      <button
        type="button"
        onClick={() => mark.mutate()}
        disabled={mark.isPending}
        className="ml-auto px-2 py-1 rounded border border-accent text-accent hover:bg-accent/20 disabled:opacity-50"
      >
        {mark.isPending ? "Marking…" : "Mark as duplicate"}
      </button>
      {mark.isError ? (
        <span className="text-accent w-full">Failed: {String(mark.error)}</span>
      ) : null}
    </div>
  );
}

function Header({ count }: { count?: number }) {
  return (
    <div className="flex items-baseline justify-between mb-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">
        Has this happened before?
      </div>
      {count !== undefined ? (
        <div className="text-[10px] text-muted">
          {count} {count === 1 ? "match" : "matches"}
        </div>
      ) : null}
    </div>
  );
}

function SimilarRow({ m }: { m: SimilarIncident }) {
  // Surface each match's resolution status — that's the high-value
  // signal: "this happened 2 weeks ago, AND it was resolved (here's the
  // root cause)" vs "this happened 2 weeks ago and is still open."
  // Implicit-open responses come back with status='open' so we always
  // get a badge to show, never an empty corner.
  const { data: resolution } = useQuery({
    queryKey: ["resolution", m.session_id],
    queryFn: () => getResolution(m.session_id),
    staleTime: 30_000,
  });

  return (
    <Link
      to={`/sessions/${encodeURIComponent(m.session_id)}`}
      className="block rounded border border-border p-2 hover:border-accent transition-colors"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="font-mono text-xs truncate">{m.session_id}</div>
        <div className="shrink-0 flex items-center gap-1.5">
          {resolution ? <ResolutionBadge status={resolution.status} /> : null}
          <div className="text-xs px-1.5 py-0.5 rounded bg-accent/20 text-accent font-mono">
            {scorePct(m.score)}
          </div>
        </div>
      </div>
      <div className="text-[11px] text-muted mt-1">
        <span className="font-mono">{m.robot_id}</span>
        {m.subsystem ? <> · {m.subsystem}</> : null}
        {m.label ? <> · {m.label}</> : null}
        {" · "}
        {relativeTime(m.started_at)}
      </div>
      {resolution?.root_cause ? (
        <div className="text-xs text-text/80 mt-1 line-clamp-2">
          <span className="text-muted">Root cause:</span> {resolution.root_cause}
        </div>
      ) : m.summary ? (
        <div className="text-xs text-text/80 mt-1 line-clamp-2">{m.summary}</div>
      ) : null}
    </Link>
  );
}
