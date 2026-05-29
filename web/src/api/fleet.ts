/**
 * v2 P3.5.6b — fleet incident stats client.
 *
 * One endpoint, one big rollup. Everything the dashboard renders comes
 * from this response — KPI tiles, sparkline trend, top patterns, robot
 * breakdown.
 */

export interface CaptureByDay {
  /** UTC date in YYYY-MM-DD form, zero-filled across the window. */
  day: string;
  count: number;
}

export interface CaptureByRobot {
  robot_id: string;
  count: number;
}

export interface ResolutionBreakdown {
  open: number;
  investigating: number;
  resolved: number;
  duplicate: number;
  wont_fix: number;
  /** Terminal / total. Null when there are no captures in the window. */
  resolution_rate: number | null;
}

export interface RecurrenceStats {
  duplicates_marked: number;
  /** Duplicates / total captures. Null when there are no captures. */
  recurrence_rate: number | null;
}

export interface TopPattern {
  /** The session label (rule name) grouped on. Null = manual saves. */
  pattern: string | null;
  count: number;
  open: number;
  investigating: number;
  resolved: number;
  duplicate: number;
  wont_fix: number;
}

export interface FleetIncidentStats {
  window_days: number;
  window_start_ms: number;
  window_end_ms: number;
  captures: {
    total: number;
    by_day: CaptureByDay[];
    by_robot: CaptureByRobot[];
  };
  resolution: ResolutionBreakdown;
  /** Mean time-to-resolution in milliseconds. Null when no terminal sessions. */
  mttr_ms: number | null;
  /** Sample size that contributed to mttr_ms. */
  mttr_n: number;
  recurrence: RecurrenceStats;
  top_patterns: TopPattern[];
}

export async function getIncidentStats(
  windowDays = 30,
): Promise<FleetIncidentStats> {
  const r = await fetch(
    `/api/v2/fleet/incident-stats?window_days=${windowDays}`,
  );
  if (!r.ok) throw new Error(`getIncidentStats: ${r.status}`);
  return r.json();
}
