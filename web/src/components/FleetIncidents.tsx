/**
 * v2 P3.5.6 — Fleet incident dashboard.
 *
 * The buyable surface. A VP of Engineering opens this on day-one of a
 * pilot and the page tells them — at a glance, no clicking required —
 * what their fleet's incident health looks like and where their ops
 * team is burning time. Built around four KPIs:
 *
 *   Captures       — volume + per-day sparkline + per-robot breakdown
 *   Resolution     — % terminal, plus the per-status count breakdown
 *   MTTR           — mean time to first resolution (the time-saved KPI)
 *   Recurrence     — % of captures explicitly marked as duplicates
 *
 * Plus a top-patterns list — the failure modes burning the most ops
 * cycles, with their open/resolved breakdown inline.
 *
 * The empty state (fresh hub, no captures yet) renders the same shell
 * with "no data" messaging on each tile, so a customer sees the
 * dashboard's structure immediately rather than a blank page.
 *
 * Numbers are intentionally precise (e.g. "62% resolved" not "good")
 * — the buyer needs the literal data, not a sentiment summary.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  getIncidentStats,
  type CaptureByDay,
  type FleetIncidentStats,
  type TopPattern,
} from "../api/fleet";
import { Card } from "./ui/Card";
import { Skeleton } from "./ui/Skeleton";

const WINDOW_OPTIONS = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
];

export function FleetIncidents() {
  const [windowDays, setWindowDays] = useState(30);

  const { data, isLoading, error } = useQuery({
    queryKey: ["fleet-incident-stats", windowDays],
    queryFn: () => getIncidentStats(windowDays),
    refetchInterval: 60_000,
  });

  return (
    <div className="p-4 grid gap-4 max-w-6xl">
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-lg">Fleet incident health</h2>
        <WindowSelector value={windowDays} onChange={setWindowDays} />
        {data ? (
          <span className="text-xs text-muted ml-auto">
            {data.captures.total} {data.captures.total === 1 ? "capture" : "captures"}{" "}
            in the last {data.window_days} days
          </span>
        ) : null}
      </div>

      {error ? (
        <Card className="bg-bg/50">
          <div className="text-sm text-accent">
            Failed to load fleet stats: {String(error)}
          </div>
        </Card>
      ) : null}

      {/* Top row: 4 KPI tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiTile label="Captures" loading={isLoading}>
          {data ? (
            <BigNumber value={data.captures.total} suffix="" />
          ) : null}
        </KpiTile>
        <KpiTile label="Resolution rate" loading={isLoading}>
          {data ? (
            <BigNumber
              value={
                data.resolution.resolution_rate === null
                  ? null
                  : Math.round(data.resolution.resolution_rate * 100)
              }
              suffix="%"
              empty="No captures"
            />
          ) : null}
        </KpiTile>
        <KpiTile label="MTTR" loading={isLoading} subtitle={`n=${data?.mttr_n ?? 0}`}>
          {data ? <Duration ms={data.mttr_ms} /> : null}
        </KpiTile>
        <KpiTile label="Recurrence rate" loading={isLoading} subtitle="explicit duplicates">
          {data ? (
            <BigNumber
              value={
                data.recurrence.recurrence_rate === null
                  ? null
                  : Math.round(data.recurrence.recurrence_rate * 100)
              }
              suffix="%"
              empty="No captures"
            />
          ) : null}
        </KpiTile>
      </div>

      {/* Estimated value: recurrence -> re-investigation time avoided.
          Turns the recurrence KPI into a budget justification. Assumptions
          are editable so a prospect can plug in their own numbers live. */}
      {data ? <ValueCard duplicates={data.recurrence.duplicates_marked} /> : null}

      {/* Captures trend */}
      <Card>
        <div className="text-[10px] uppercase tracking-wide text-muted mb-2">
          Captures per day
        </div>
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : data && data.captures.by_day.length > 0 ? (
          <Sparkline series={data.captures.by_day} />
        ) : (
          <div className="text-xs text-muted">No captures in this window.</div>
        )}
      </Card>

      {/* Resolution breakdown */}
      <Card>
        <div className="text-[10px] uppercase tracking-wide text-muted mb-2">
          Resolution breakdown
        </div>
        {isLoading ? (
          <Skeleton className="h-6 w-full" />
        ) : data ? (
          <ResolutionBar data={data} />
        ) : null}
      </Card>

      {/* Two-col: top patterns + per-robot */}
      <div className="grid md:grid-cols-2 gap-3">
        <Card>
          <div className="text-[10px] uppercase tracking-wide text-muted mb-2">
            Top recurring patterns
          </div>
          {isLoading ? (
            <>
              <Skeleton className="h-6 w-full mb-2" />
              <Skeleton className="h-6 w-full mb-2" />
              <Skeleton className="h-6 w-full" />
            </>
          ) : data && data.top_patterns.length > 0 ? (
            <div className="grid gap-2">
              {data.top_patterns.map((p, i) => (
                <PatternRow key={`${p.pattern ?? "_null"}-${i}`} p={p} />
              ))}
            </div>
          ) : (
            <div className="text-xs text-muted">
              No patterns yet. As captures accumulate, the most-frequent
              failure modes surface here ranked by volume.
            </div>
          )}
        </Card>

        <Card>
          <div className="text-[10px] uppercase tracking-wide text-muted mb-2">
            Captures by robot
          </div>
          {isLoading ? (
            <>
              <Skeleton className="h-6 w-full mb-2" />
              <Skeleton className="h-6 w-full" />
            </>
          ) : data && data.captures.by_robot.length > 0 ? (
            <div className="grid gap-1">
              {data.captures.by_robot.map((r) => (
                <RobotRow key={r.robot_id} robot_id={r.robot_id} count={r.count} max={data.captures.by_robot[0].count} />
              ))}
            </div>
          ) : (
            <div className="text-xs text-muted">No robots reporting.</div>
          )}
        </Card>
      </div>
    </div>
  );
}

// ============================================================
// Subcomponents
// ============================================================

function ValueCard({ duplicates }: { duplicates: number }) {
  // Each operator-marked duplicate is an incident the team recognised as
  // already-seen instead of re-investigating from scratch. duplicates ×
  // hours-per-investigation = time avoided. Conservative on purpose: it
  // counts only explicit duplicates, not similarity-suggested ones.
  const [hoursPer, setHoursPer] = useState(4);
  const [rate, setRate] = useState<number | "">("");
  const hoursSaved = duplicates * hoursPer;
  const dollars = typeof rate === "number" ? hoursSaved * rate : null;

  return (
    <Card>
      <div className="text-[10px] uppercase tracking-wide text-muted mb-2">
        Estimated value — repeat-incident time avoided
      </div>
      {duplicates === 0 ? (
        <div className="text-xs text-muted">
          No incidents marked as duplicates in this window yet. Once your team
          recognises a repeat instead of re-investigating it, the time saved
          shows up here.
        </div>
      ) : (
        <div className="grid gap-3">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-2xl font-mono text-accent">{hoursSaved}h</span>
            <span className="text-sm text-muted">of re-investigation avoided</span>
            {dollars !== null ? (
              <span className="text-2xl font-mono text-accent ml-2">
                ${dollars.toLocaleString()}
              </span>
            ) : null}
          </div>
          <div className="text-xs text-muted">
            {duplicates} {duplicates === 1 ? "incident" : "incidents"} recognised
            as a repeat this window — each one a re-investigation your team
            didn't redo from scratch.
          </div>
          <div className="flex items-center gap-4 flex-wrap text-xs">
            <label className="flex items-center gap-1.5 text-muted">
              hrs / investigation
              <input
                type="number"
                min={0}
                step={0.5}
                value={hoursPer}
                onChange={(e) => setHoursPer(Math.max(0, Number(e.target.value) || 0))}
                className="w-16 bg-bg border border-border rounded px-1.5 py-0.5 font-mono outline-none focus:border-accent"
              />
            </label>
            <label className="flex items-center gap-1.5 text-muted">
              $ / hr (optional)
              <input
                type="number"
                min={0}
                step={1}
                value={rate}
                placeholder="—"
                onChange={(e) => {
                  const v = e.target.value;
                  setRate(v === "" ? "" : Math.max(0, Number(v) || 0));
                }}
                className="w-20 bg-bg border border-border rounded px-1.5 py-0.5 font-mono outline-none focus:border-accent"
              />
            </label>
          </div>
          <div className="text-[10px] text-muted/70">
            Counts operator-marked duplicates only — a deliberately conservative
            floor. Adjust the assumptions to your team's numbers.
          </div>
        </div>
      )}
    </Card>
  );
}

function WindowSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <div className="flex gap-1 text-xs">
      {WINDOW_OPTIONS.map((opt) => (
        <button
          key={opt.days}
          onClick={() => onChange(opt.days)}
          className={`px-2 py-1 rounded border ${
            value === opt.days
              ? "bg-accent/20 text-accent border-accent"
              : "border-border text-muted hover:text-text"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function KpiTile({
  label,
  subtitle,
  loading,
  children,
}: {
  label: string;
  subtitle?: string;
  loading?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <Card className="bg-bg/50">
      <div className="flex items-baseline justify-between mb-1">
        <div className="text-[10px] uppercase tracking-wide text-muted">
          {label}
        </div>
        {subtitle ? (
          <div className="text-[10px] text-muted">{subtitle}</div>
        ) : null}
      </div>
      {loading ? <Skeleton className="h-7 w-16" /> : children}
    </Card>
  );
}

function BigNumber({
  value,
  suffix,
  empty = "—",
}: {
  value: number | null;
  suffix: string;
  empty?: string;
}) {
  if (value === null) {
    return <div className="text-sm text-muted">{empty}</div>;
  }
  return (
    <div className="text-2xl font-mono">
      {value}
      <span className="text-base text-muted">{suffix}</span>
    </div>
  );
}

function Duration({ ms }: { ms: number | null }) {
  if (ms === null) return <div className="text-sm text-muted">No data</div>;
  const sec = ms / 1000;
  if (sec < 60) return <div className="text-2xl font-mono">{sec.toFixed(0)}s</div>;
  const min = sec / 60;
  if (min < 60) return <div className="text-2xl font-mono">{min.toFixed(1)}m</div>;
  const hr = min / 60;
  if (hr < 48) return <div className="text-2xl font-mono">{hr.toFixed(1)}h</div>;
  return <div className="text-2xl font-mono">{(hr / 24).toFixed(1)}d</div>;
}

/**
 * Plain 2D-canvas sparkline. Same primitive style as the v2 P1.7.4
 * scalar charts (canvas, not Pixi/WebGL — avoids the Chrome WebGL
 * context cap entirely).
 */
function Sparkline({ series }: { series: CaptureByDay[] }) {
  const max = series.reduce((m, d) => Math.max(m, d.count), 0);
  if (max === 0) {
    return <div className="text-xs text-muted">Zero captures in this window.</div>;
  }
  // Render as inline SVG so it scales naturally and stays crisp.
  const W = 600;
  const H = 64;
  const stepX = series.length > 1 ? W / (series.length - 1) : 0;
  const points = series.map((d, i) => {
    const x = i * stepX;
    const y = H - (d.count / max) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-16 stroke-accent fill-none"
        preserveAspectRatio="none"
      >
        <polyline points={points.join(" ")} strokeWidth={1.5} />
      </svg>
      <div className="flex justify-between text-[10px] text-muted mt-1">
        <span>{series[0]?.day}</span>
        <span>peak: {max}</span>
        <span>{series[series.length - 1]?.day}</span>
      </div>
    </div>
  );
}

function ResolutionBar({ data }: { data: FleetIncidentStats }) {
  const { open, investigating, resolved, duplicate, wont_fix } = data.resolution;
  const total = open + investigating + resolved + duplicate + wont_fix;
  if (total === 0) {
    return <div className="text-xs text-muted">No captures yet.</div>;
  }
  const seg = (n: number) => `${(n / total) * 100}%`;
  return (
    <div>
      <div className="flex h-3 rounded overflow-hidden border border-border">
        <div style={{ width: seg(resolved) }} className="bg-green-500/60" title={`resolved: ${resolved}`} />
        <div style={{ width: seg(duplicate) }} className="bg-blue-500/60" title={`duplicate: ${duplicate}`} />
        <div style={{ width: seg(wont_fix) }} className="bg-gray-500/60" title={`wont_fix: ${wont_fix}`} />
        <div style={{ width: seg(investigating) }} className="bg-amber-500/60" title={`investigating: ${investigating}`} />
        <div style={{ width: seg(open) }} className="bg-red-500/60" title={`open: ${open}`} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mt-2 text-xs">
        <Legend color="bg-green-500/60" label="Resolved" n={resolved} />
        <Legend color="bg-blue-500/60" label="Duplicate" n={duplicate} />
        <Legend color="bg-gray-500/60" label="Wont fix" n={wont_fix} />
        <Legend color="bg-amber-500/60" label="Investigating" n={investigating} />
        <Legend color="bg-red-500/60" label="Open" n={open} />
      </div>
    </div>
  );
}

function Legend({ color, label, n }: { color: string; label: string; n: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={`inline-block w-2.5 h-2.5 rounded ${color}`} />
      <span className="text-muted">{label}:</span>
      <span className="font-mono">{n}</span>
    </div>
  );
}

function PatternRow({ p }: { p: TopPattern }) {
  const label = p.pattern ?? "(unlabelled)";
  return (
    <div className="border border-border rounded p-2">
      <div className="flex items-center justify-between">
        <div className="font-mono text-sm truncate">{label}</div>
        <div className="text-xs px-1.5 py-0.5 rounded bg-accent/20 text-accent font-mono shrink-0">
          {p.count}
        </div>
      </div>
      <div className="text-[11px] text-muted mt-1 flex gap-2 flex-wrap">
        {p.open > 0 ? <span>{p.open} open</span> : null}
        {p.investigating > 0 ? <span>{p.investigating} investigating</span> : null}
        {p.resolved > 0 ? <span>{p.resolved} resolved</span> : null}
        {p.duplicate > 0 ? <span>{p.duplicate} duplicate</span> : null}
        {p.wont_fix > 0 ? <span>{p.wont_fix} wont-fix</span> : null}
      </div>
    </div>
  );
}

function RobotRow({
  robot_id,
  count,
  max,
}: {
  robot_id: string;
  count: number;
  max: number;
}) {
  const pct = max > 0 ? (count / max) * 100 : 0;
  return (
    <Link
      to={`/?robot=${encodeURIComponent(robot_id)}`}
      className="grid grid-cols-[1fr_auto] gap-2 items-center hover:bg-bg/50 rounded px-1 py-0.5"
    >
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs truncate w-32">{robot_id}</span>
        <div className="flex-1 h-1.5 bg-bg rounded overflow-hidden border border-border">
          <div
            className="h-full bg-accent/60"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      <span className="text-xs font-mono text-muted">{count}</span>
    </Link>
  );
}
