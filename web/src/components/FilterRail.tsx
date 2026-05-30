import { useMemo, useState } from "react";
import type { AgentInfo } from "../api/agents";
import type { SessionSummary } from "../api/sessions";

/**
 * Left-rail navigation for the SessionList. Groups robots by subsystem
 * (free-form per Hard Rule 23 in v2-SPEC) so 50-robot fleets stay
 * navigable.
 *
 * Data sources:
 *   - agents: full roster of robots that have ever reported a heartbeat
 *     (from /api/v1/agents — includes subsystem)
 *   - sessions: currently-visible sessions in the list (the unfiltered
 *     subset gives us session counts per robot)
 *
 * Selection model:
 *   - `subsystem` selected      → filter to that subsystem's robots
 *   - `robot` selected          → filter to that robot only
 *   - both selected             → robot wins (it's more specific)
 *   - neither                   → show all
 *
 * Single-robot deployments (≤1 robot AND no subsystems present) get
 * `null` from the `shouldRender` helper, letting SessionList hide the
 * rail entirely. Hard Rule 18: v1.5 single-robot UI is untouched.
 */

interface Props {
  agents: AgentInfo[];
  /** All sessions visible right now — used to count per-robot. */
  sessions: SessionSummary[];
  /** All robot_ids the API reported, for fallback when no agents
   *  registered (e.g. fixture sessions, legacy v1.5 sessions). */
  robotIds: string[];
  selectedSubsystem: string | null;
  selectedRobot: string | null;
  onSelectSubsystem: (s: string | null) => void;
  onSelectRobot: (r: string | null) => void;
}

const NO_SUBSYSTEM_KEY = "__nosubsys__"; // internal key for the "no subsystem" bucket

interface SubsystemGroup {
  key: string;       // NO_SUBSYSTEM_KEY for the unspecified bucket
  label: string;     // display name; "no subsystem" for the unspecified bucket
  robots: string[];  // robot_ids in this group
  count: number;     // total sessions across this group's robots
}

/**
 * Aggregate agents + sessions + robot_ids into a tree:
 *   {subsystem -> [robots]}
 */
function buildGroups(
  agents: AgentInfo[],
  sessions: SessionSummary[],
  robotIds: string[],
): SubsystemGroup[] {
  // robot -> subsystem
  const robotSubsystem = new Map<string, string | null>();
  for (const a of agents) {
    robotSubsystem.set(a.robot_id, a.subsystem ?? null);
  }
  // Sessions can carry subsystem too (P1.6); use as fallback for
  // robots that don't have an agent record yet.
  for (const s of sessions) {
    if (!robotSubsystem.has(s.robot_id)) {
      robotSubsystem.set(s.robot_id, s.subsystem ?? null);
    }
  }
  // Robots seen via /api/sessions but neither agent nor session
  // declared a subsystem (fixture sessions, very legacy).
  for (const rid of robotIds) {
    if (!robotSubsystem.has(rid)) {
      robotSubsystem.set(rid, null);
    }
  }

  // Group: subsystem -> Set(robot)
  const groups = new Map<string, Set<string>>();
  for (const [rid, sub] of robotSubsystem.entries()) {
    const key = sub ?? NO_SUBSYSTEM_KEY;
    if (!groups.has(key)) groups.set(key, new Set());
    groups.get(key)!.add(rid);
  }

  // Session counts per robot, computed from the currently-loaded sessions.
  const robotCount = new Map<string, number>();
  for (const s of sessions) {
    robotCount.set(s.robot_id, (robotCount.get(s.robot_id) ?? 0) + 1);
  }

  const out: SubsystemGroup[] = [];
  for (const [key, robotsSet] of groups.entries()) {
    const robotsArr = Array.from(robotsSet).sort();
    const count = robotsArr.reduce(
      (n, r) => n + (robotCount.get(r) ?? 0),
      0,
    );
    out.push({
      key,
      label: key === NO_SUBSYSTEM_KEY ? "no subsystem" : key,
      robots: robotsArr,
      count,
    });
  }
  // Sort: real subsystems first (alphabetical), no-subsystem bucket last.
  out.sort((a, b) => {
    if (a.key === NO_SUBSYSTEM_KEY) return 1;
    if (b.key === NO_SUBSYSTEM_KEY) return -1;
    return a.label.localeCompare(b.label);
  });
  return out;
}

/**
 * Decide whether the rail is worth showing. Returns the computed groups
 * if >=2 robots OR any subsystem is set; otherwise null (caller hides
 * the rail).
 */
export function useRailGroups(
  agents: AgentInfo[],
  sessions: SessionSummary[],
  robotIds: string[],
): SubsystemGroup[] | null {
  return useMemo(() => {
    const groups = buildGroups(agents, sessions, robotIds);
    const totalRobots = groups.reduce((n, g) => n + g.robots.length, 0);
    const anySubsystem = groups.some((g) => g.key !== NO_SUBSYSTEM_KEY);
    if (totalRobots < 2 && !anySubsystem) return null;
    return groups;
  }, [agents, sessions, robotIds]);
}

export function FilterRail({
  agents,
  sessions,
  robotIds,
  selectedSubsystem,
  selectedRobot,
  onSelectSubsystem,
  onSelectRobot,
}: Props) {
  const groups = useRailGroups(agents, sessions, robotIds);
  // Subsystem groups collapsed/expanded — default expanded if any
  // descendant is selected, otherwise expanded for ≤3 groups so the
  // user sees the structure.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const toggle = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  if (groups === null) return null;

  const totalSessions = sessions.length;

  return (
    <aside className="w-56 shrink-0 border-r border-border pr-3 grid gap-1 text-sm content-start">
      <button
        type="button"
        onClick={() => {
          onSelectSubsystem(null);
          onSelectRobot(null);
        }}
        className={`text-left px-2 py-1 rounded border ${
          !selectedSubsystem && !selectedRobot
            ? "bg-accent/20 text-accent border-accent"
            : "border-border hover:border-accent text-muted hover:text-text"
        }`}
      >
        <div className="flex items-center justify-between">
          <span>all</span>
          <span className="text-xs">{totalSessions}</span>
        </div>
      </button>

      {groups.map((g) => {
        const isCollapsed = collapsed.has(g.key);
        const subsysActive =
          selectedSubsystem === g.label && !selectedRobot;
        return (
          <div key={g.key} className="grid gap-0.5">
            <div className="flex items-stretch gap-1">
              <button
                type="button"
                onClick={() => toggle(g.key)}
                className="text-muted hover:text-text px-1"
                aria-label={isCollapsed ? "Expand" : "Collapse"}
              >
                {isCollapsed ? "▸" : "▾"}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (g.key === NO_SUBSYSTEM_KEY) {
                    // No subsystem to select — clear subsystem filter
                    onSelectSubsystem(null);
                    onSelectRobot(null);
                  } else {
                    onSelectSubsystem(g.label);
                    onSelectRobot(null);
                  }
                }}
                className={`flex-1 text-left px-2 py-1 rounded border ${
                  subsysActive
                    ? "bg-accent/20 text-accent border-accent"
                    : "border-border hover:border-accent text-muted hover:text-text"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono truncate">{g.label}</span>
                  <span className="text-xs">{g.count}</span>
                </div>
              </button>
            </div>
            {!isCollapsed
              ? g.robots.map((r) => {
                  const active = selectedRobot === r;
                  return (
                    <button
                      key={r}
                      type="button"
                      onClick={() => {
                        // Filter by robot id alone. A robot id is unique, so
                        // the group's subsystem adds nothing — and worse, the
                        // rail groups robots by their *agent heartbeat*
                        // subsystem while the session list filters by each
                        // session row's subsystem. When those disagree (e.g.
                        // an agent reports "test-fleet" but its locally-saved
                        // sessions have a null subsystem), also forcing the
                        // subsystem filter here yields a dead-end empty list.
                        onSelectSubsystem(null);
                        onSelectRobot(r);
                      }}
                      className={`ml-5 text-left px-2 py-0.5 rounded border ${
                        active
                          ? "bg-accent/20 text-accent border-accent"
                          : "border-border hover:border-accent text-muted hover:text-text"
                      }`}
                    >
                      <span className="font-mono text-xs">{r}</span>
                    </button>
                  );
                })
              : null}
          </div>
        );
      })}
    </aside>
  );
}
