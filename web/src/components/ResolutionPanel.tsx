/**
 * v2 P3.5.6c — per-session resolution editor.
 *
 * The operator workflow that populates the fleet incident dashboard's
 * KPIs. Lives as a card on session detail. Three jobs:
 *
 *   1. Show the current resolution state at a glance (status badge +
 *      root-cause excerpt + ticket link if any)
 *   2. Let the operator transition the session through statuses
 *      (open → investigating → resolved/duplicate/wont_fix) and edit
 *      the root cause + ticket inline
 *   3. Surface the resolved_at timestamp once terminal — gives the
 *      operator confidence "this session is recorded as fixed at T"
 *
 * The dashboard recurrence + MTTR KPIs depend on operators actually
 * using this. Friction here = empty KPIs = "this is just rosbag with
 * extra steps." So the editor is single-click for the common case:
 * the status pill IS the dropdown; root cause + ticket are inline
 * textareas; save is a single button.
 *
 * The empty (implicit-open) state is the default; we never show "no
 * resolution yet" as a blocker. The "Mark resolved" action takes one
 * click to write the row for the first time.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  RESOLUTION_STATUSES,
  type Resolution,
  type ResolutionStatus,
  deleteResolution,
  getResolution,
  putResolution,
} from "../api/sessions";
import { Card } from "./ui/Card";
import { Skeleton } from "./ui/Skeleton";

interface Props {
  sessionId: string;
}

const STATUS_LABELS: Record<ResolutionStatus, string> = {
  open: "Open",
  investigating: "Investigating",
  resolved: "Resolved",
  duplicate: "Duplicate",
  wont_fix: "Wont fix",
};

const STATUS_COLORS: Record<ResolutionStatus, string> = {
  open: "bg-red-500/20 text-red-400 border-red-500/40",
  investigating: "bg-amber-500/20 text-amber-400 border-amber-500/40",
  resolved: "bg-green-500/20 text-green-400 border-green-500/40",
  duplicate: "bg-blue-500/20 text-blue-400 border-blue-500/40",
  wont_fix: "bg-gray-500/20 text-gray-400 border-gray-500/40",
};

function formatTime(ms: number | null): string {
  if (!ms) return "—";
  return new Date(ms).toLocaleString();
}

export function ResolutionPanel({ sessionId }: Props) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["resolution", sessionId],
    queryFn: () => getResolution(sessionId),
    staleTime: 30_000,
  });

  // Local editable state for the form fields. Synced from `data` on
  // every fetch; the operator's in-flight edits override until Save.
  const [status, setStatus] = useState<ResolutionStatus>("open");
  const [rootCause, setRootCause] = useState("");
  const [linkedTicket, setLinkedTicket] = useState("");
  const [duplicateOf, setDuplicateOf] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!data) return;
    if (dirty) return; // don't clobber an active edit on refetch
    setStatus(data.status);
    setRootCause(data.root_cause ?? "");
    setLinkedTicket(data.linked_ticket ?? "");
    setDuplicateOf(data.duplicate_of ?? "");
  }, [data, dirty]);

  const save = useMutation({
    mutationFn: () =>
      putResolution(sessionId, {
        status,
        root_cause: rootCause || null,
        linked_ticket: linkedTicket || null,
        duplicate_of: status === "duplicate" ? duplicateOf || null : null,
      }),
    onSuccess: (saved: Resolution) => {
      qc.setQueryData(["resolution", sessionId], saved);
      // Fleet stats roll up resolution counts + MTTR — refetch so the
      // dashboard reflects an edit made here.
      qc.invalidateQueries({ queryKey: ["fleet-incident-stats"] });
      qc.invalidateQueries({ queryKey: ["similar", sessionId] });
      setDirty(false);
    },
  });

  const reset = useMutation({
    mutationFn: () => deleteResolution(sessionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["resolution", sessionId] });
      qc.invalidateQueries({ queryKey: ["fleet-incident-stats"] });
      setDirty(false);
    },
  });

  if (isLoading) {
    return (
      <Card className="bg-bg/50">
        <Header />
        <Skeleton className="h-6 w-32 mb-2" />
        <Skeleton className="h-16 w-full" />
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card className="bg-bg/50">
        <Header />
        <div className="text-xs text-accent">
          Failed to load resolution: {String(error)}
        </div>
      </Card>
    );
  }

  // edited_at === 0 is the implicit-open sentinel — server has no row yet.
  const isImplicit = data.edited_at === 0;

  return (
    <Card className="bg-bg/50">
      <Header
        right={
          !isImplicit ? (
            <span className="text-[10px] text-muted">
              Last edited {formatTime(data.edited_at)}
              {data.edited_by ? ` by ${data.edited_by}` : ""}
            </span>
          ) : null
        }
      />

      <div className="grid gap-2">
        <label className="grid gap-1">
          <span className="text-[10px] uppercase tracking-wide text-muted">
            Status
          </span>
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as ResolutionStatus);
              setDirty(true);
            }}
            className={`px-2 py-1 rounded border font-mono text-sm ${STATUS_COLORS[status]}`}
          >
            {RESOLUTION_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-1">
          <span className="text-[10px] uppercase tracking-wide text-muted">
            Root cause
          </span>
          <textarea
            value={rootCause}
            onChange={(e) => {
              setRootCause(e.target.value);
              setDirty(true);
            }}
            placeholder="What caused this? Free text — appears alongside the session in the dashboard and on every duplicate it's linked from."
            className="px-2 py-1 rounded border border-border bg-bg text-sm font-mono resize-y min-h-[60px]"
            rows={3}
          />
        </label>

        <label className="grid gap-1">
          <span className="text-[10px] uppercase tracking-wide text-muted">
            Linked ticket (optional)
          </span>
          <input
            type="text"
            value={linkedTicket}
            onChange={(e) => {
              setLinkedTicket(e.target.value);
              setDirty(true);
            }}
            placeholder="Jira id, GitHub issue URL, Linear ticket — anything"
            className="px-2 py-1 rounded border border-border bg-bg text-sm font-mono"
          />
        </label>

        {status === "duplicate" ? (
          <label className="grid gap-1">
            <span className="text-[10px] uppercase tracking-wide text-muted">
              Duplicate of (session id)
            </span>
            <input
              type="text"
              value={duplicateOf}
              onChange={(e) => {
                setDuplicateOf(e.target.value);
                setDirty(true);
              }}
              placeholder="Required for duplicate status — paste the canonical session id"
              className="px-2 py-1 rounded border border-border bg-bg text-sm font-mono"
            />
          </label>
        ) : null}

        {data.resolved_at ? (
          <div className="text-[11px] text-muted">
            Resolved at {formatTime(data.resolved_at)} (counts toward fleet MTTR)
          </div>
        ) : null}

        {save.error ? (
          <div className="text-xs text-accent">
            {(save.error as Error).message}
          </div>
        ) : null}

        <div className="flex gap-2 mt-1 flex-wrap">
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="px-3 py-1 text-sm rounded bg-accent/20 text-accent border border-accent disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          {!isImplicit ? (
            <button
              onClick={() => reset.mutate()}
              disabled={reset.isPending}
              className="px-3 py-1 text-sm rounded border border-border text-muted hover:text-text disabled:opacity-50"
              title="Revert this session to the implicit 'open' state by clearing its resolution row"
            >
              {reset.isPending ? "Clearing…" : "Clear resolution"}
            </button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function Header({ right }: { right?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between mb-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">
        Resolution
      </div>
      {right}
    </div>
  );
}

/** Lightweight status pill — exported for re-use in SimilarIncidents
 * so each past-incident card surfaces its resolution at a glance. */
export function ResolutionBadge({ status }: { status: ResolutionStatus }) {
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${STATUS_COLORS[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}
