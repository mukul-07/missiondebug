/**
 * v2 — "Topics on this robot": live ROS topic discovery for one agent,
 * rendered inside an expanded row on the Fleet health page.
 *
 * Data comes from the hub proxy (GET /api/v1/agents/{robot_id}/topics),
 * which forwards to the agent's GET /topics (agent >= 0.7.0). The panel is
 * strictly read-only toward the robot (Hard Rule 22): the checkboxes drive a
 * "Copy config YAML" generator the operator applies to the agent's
 * config.yaml themselves — the hub never writes config back.
 *
 * Discovery semantics honored here:
 *  - settled=false means a partial DDS scan. We keep showing the last
 *    settled list (with a "settling" note) instead of rendering the partial
 *    one as truth, and auto-rescan a couple of times — the agent's first
 *    scan after a restart always reports unsettled by design.
 *  - resolvable=false means the topic's message package isn't built/sourced
 *    on the robot: capture would silently skip it. That's the red badge.
 *  - publishers=0 means advertised-but-silent (often a ghost of a dead node
 *    or our own capture subscription's echo).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  type AgentTopic,
  type AgentTopics,
  AgentTopicsError,
  getAgentTopics,
} from "../api/agents";
import { copyText } from "../lib/clipboard";
import { Button } from "./ui/Button";
import { Skeleton } from "./ui/Skeleton";

// Checkbox selections per robot, module-scoped so an open panel that
// remounts (e.g. its row moves between health sections when the robot's
// status flips) keeps the operator's edits. Session-lifetime only — a
// page reload re-derives the pre-checks from the scan's recommendations.
const checkedByRobot = new Map<string, Set<string>>();

const CATEGORY_ORDER = [
  "control",
  "state",
  "safety",
  "plan",
  "transform",
  "perception",
  "other",
  "debug",
];

const CATEGORY_LABEL: Record<string, string> = {
  control: "Control",
  state: "State / telemetry",
  safety: "Safety / health",
  plan: "Plan / goal",
  transform: "Transforms",
  perception: "Perception",
  other: "Other",
  debug: "Debug / diagnostics",
};

const BADGE_BASE = "text-[10px] px-1.5 py-0.5 rounded border font-mono whitespace-nowrap";

/** Topic discovery shipped in agent 0.7.0. null = version unknown, try anyway. */
function supportsDiscovery(version: string | null): boolean | null {
  if (!version) return null;
  const m = version.match(/^(\d+)\.(\d+)/);
  if (!m) return null;
  const major = Number(m[1]);
  const minor = Number(m[2]);
  return major > 0 || minor >= 7;
}

function configYaml(topics: AgentTopic[], checked: Set<string>): string {
  const lines = [
    "# MissionDebug capture topics — replace the topics: block in",
    "# /etc/missiondebug/config.yaml on the robot, then restart the agent.",
    "topics:",
  ];
  for (const t of topics) {
    if (checked.has(t.name)) {
      lines.push(`  - { name: "${t.name}", type: "${t.type}" }`);
    }
  }
  return lines.join("\n") + "\n";
}

export function AgentTopicsPanel({
  robotId,
  agentVersion,
}: {
  robotId: string;
  agentVersion: string | null;
}) {
  const supported = supportsDiscovery(agentVersion);

  const q = useQuery({
    queryKey: ["agent-topics", robotId],
    queryFn: () => getAgentTopics(robotId),
    enabled: supported !== false,
    staleTime: 30_000,
    retry: false, // 409/426 never heal on retry; the Rescan button covers 502s
  });

  // Keep the last settled snapshot so a partial scan never replaces a
  // complete view (per the agent's discovery contract).
  const lastSettledRef = useRef<AgentTopics | null>(null);
  useEffect(() => {
    if (q.data?.settled) lastSettledRef.current = q.data;
  }, [q.data]);

  // The first scan after an agent restart always reports settled=false;
  // a warm rescan settles in well under a second. Auto-rescan twice.
  const [autoRetries, setAutoRetries] = useState(0);
  useEffect(() => {
    if (!q.data || q.data.settled || autoRetries >= 2) return;
    const t = window.setTimeout(() => {
      setAutoRetries((n) => n + 1);
      q.refetch();
    }, 2_500);
    return () => window.clearTimeout(t);
  }, [q.data, autoRetries, q.refetch]);

  const view =
    q.data && !q.data.settled && lastSettledRef.current
      ? lastSettledRef.current
      : q.data;

  // Agent's effective ROS env (agent >= 0.8.2; older agents send nothing).
  const rosEnv = q.data?.ros_env ?? view?.ros_env ?? null;
  // Every topic advertised but ZERO publishers anywhere: the classic sign
  // the agent is isolated from the operator's nodes (different
  // ROS_DOMAIN_ID / RMW in their terminals vs the systemd service's
  // defaults). Strict === 0 so unknown counts (null, older agents) never
  // trigger the hint.
  const allGhost = Boolean(
    view &&
      view.topics.length > 0 &&
      view.topics.every((t) => t.publishers === 0),
  );

  // Capture-selection checkboxes: pre-check recommended topics whose type
  // actually resolves on the robot (a recommended-but-unbuilt topic would
  // capture nothing). Initialized once from the first SETTLED scan — a
  // partial scan would pre-check only the topics it happened to see, and
  // the ones arriving at settle would stay unchecked. Rescans after init
  // keep the user's edits (persisted per-robot across row remounts).
  const [checked, setChecked] = useState<Set<string> | null>(
    () => checkedByRobot.get(robotId) ?? null,
  );
  useEffect(() => {
    if (view?.settled && checked === null) {
      const initial = new Set(
        view.topics
          .filter((t) => t.recommended && t.resolvable)
          .map((t) => t.name),
      );
      checkedByRobot.set(robotId, initial);
      setChecked(initial);
    }
  }, [view, checked, robotId]);

  // Substring filter over topic names — 30-70 topics in a 320px scroll
  // region is scroll-hunting without one.
  const [filter, setFilter] = useState("");
  const visibleTopics = useMemo(() => {
    if (!view) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return view.topics;
    return view.topics.filter((t) => t.name.toLowerCase().includes(q));
  }, [view, filter]);

  const groups = useMemo(() => {
    const byCat = new Map<string, AgentTopic[]>();
    for (const t of visibleTopics) {
      const cat = CATEGORY_ORDER.includes(t.category) ? t.category : "other";
      const list = byCat.get(cat) ?? [];
      list.push(t);
      byCat.set(cat, list);
    }
    return CATEGORY_ORDER.filter((c) => byCat.has(c)).map((c) => ({
      category: c,
      topics: byCat.get(c) as AgentTopic[],
    }));
  }, [visibleTopics]);

  // The selection that actually lands in the YAML: intersection of the
  // checked set with the CURRENT scan. A rescan can drop topics; the
  // button count must drop with it or it overstates what gets copied.
  const effectiveChecked = useMemo(() => {
    if (!view || !checked) return [];
    return view.topics.filter((t) => checked.has(t.name));
  }, [view, checked]);

  const [copied, setCopied] = useState(false);
  const onCopyConfig = async () => {
    if (!view || !checked || effectiveChecked.length === 0) return;
    if (await copyText(configYaml(view.topics, checked), "Copy this config:")) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  };

  const toggle = (name: string) => {
    setChecked((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      checkedByRobot.set(robotId, next);
      return next;
    });
  };

  // ---- states ----------------------------------------------------------

  if (supported === false) {
    return (
      <div className="text-xs text-muted">
        Topic discovery needs agent ≥ 0.7.0 — this robot reports{" "}
        <span className="font-mono">{agentVersion}</span>. Upgrade the agent
        to browse its live ROS topics from here.
      </div>
    );
  }

  if (q.isLoading) {
    return (
      <div className="grid gap-2">
        <div className="text-[10px] uppercase tracking-wide text-muted">
          Topics on this robot
        </div>
        <Skeleton className="h-4 w-72" />
        <Skeleton className="h-4 w-56" />
        <Skeleton className="h-4 w-64" />
      </div>
    );
  }

  if (q.isError) {
    const err = q.error;
    let message: string;
    if (err instanceof AgentTopicsError && err.status === 409) {
      message =
        "This agent didn't report a reachable URL (it may serve its API " +
        "over a Unix socket). Topic discovery needs a direct HTTP route " +
        "from the hub to the robot.";
    } else if (err instanceof AgentTopicsError && err.status === 426) {
      message = err.detail || "Topic discovery needs agent ≥ 0.7.0.";
    } else if (err instanceof AgentTopicsError && err.status === 502) {
      message =
        "The robot's agent is unreachable from the hub right now — is the " +
        "robot online?";
    } else if (err instanceof AgentTopicsError) {
      // Anything unexpected (auth 401, pruned-robot 404, ...) still gets a
      // human sentence, never a raw exception string.
      message =
        `Couldn't load topics from the hub (HTTP ${err.status}). ` +
        "Retry, or sign in again if the hub requires login.";
    } else {
      message = "Couldn't load topics — the hub didn't respond.";
    }
    const retriable = !(
      err instanceof AgentTopicsError &&
      (err.status === 409 || err.status === 426)
    );
    return (
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="text-xs text-muted">{message}</span>
        {retriable ? (
          <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => q.refetch()}>
            Retry
          </Button>
        ) : null}
      </div>
    );
  }

  if (!view) return null;

  if (view.topics.length === 0) {
    return (
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="text-xs text-muted">
          No ROS topics visible on this robot. If ROS should be running,
          check that the agent's environment is sourced.
        </span>
        <Button variant="ghost" className="text-xs px-2 py-1" onClick={() => q.refetch()}>
          Rescan
        </Button>
      </div>
    );
  }

  const showingStale = Boolean(q.data && !q.data.settled && lastSettledRef.current);
  const captured = new Set(view.last_capture_topics ?? []);
  const recommendedCount = view.topics.filter((t) => t.recommended).length;

  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-muted">
          Topics on this robot
        </span>
        <span className="text-xs text-muted">
          {filter.trim()
            ? `${visibleTopics.length} of ${view.topics.length} match`
            : `${view.topics.length} visible · ${recommendedCount} recommended`}
        </span>
        {view.last_capture_session_id ? (
          <Link
            to={`/sessions/${encodeURIComponent(view.last_capture_session_id)}`}
            className="text-xs text-green-400 hover:underline"
            title="Open this robot's most recent capture"
          >
            last capture →
          </Link>
        ) : null}
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="filter topics…"
          aria-label="Filter topics by name"
          className="ml-auto w-36 bg-bg border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent"
        />
        <Button
          variant="ghost"
          className="text-xs px-2 py-1"
          onClick={() => {
            setAutoRetries(0);
            q.refetch();
          }}
          disabled={q.isFetching}
        >
          {q.isFetching ? "Scanning…" : "↻ Rescan"}
        </Button>
      </div>

      {q.data && !q.data.settled ? (
        <div className="text-xs text-yellow-400">
          ROS discovery is settling —{" "}
          {showingStale
            ? "showing the last complete scan."
            : "this list may be incomplete."}
        </div>
      ) : null}

      {rosEnv ? (
        <div className="text-[11px] text-muted">
          agent env:{" "}
          <span className="font-mono">
            domain {rosEnv.domain_id ?? "?"}
            {rosEnv.rmw ? ` · ${rosEnv.rmw}` : ""}
            {rosEnv.distro ? ` · ${rosEnv.distro}` : ""}
          </span>
        </div>
      ) : null}

      {allGhost ? (
        <div className="text-xs text-yellow-400 border border-yellow-400/30 rounded px-2 py-1.5">
          The agent sees <b>no publishers on any topic</b> — your nodes may be
          running in a different ROS environment. In the terminal running your
          publishers, run{" "}
          <span className="font-mono">printenv | grep -E '^(ROS_|RMW_)'</span>{" "}
          and compare with the agent env above (a systemd agent runs with
          defaults: domain 0, default RMW —{" "}
          <span className="font-mono">sudo systemctl edit missiondebug-agent</span>{" "}
          to match yours).
        </div>
      ) : null}

      <div className="grid gap-2 max-h-80 overflow-y-auto pr-1">
        {filter.trim() && visibleTopics.length === 0 ? (
          <div className="text-xs text-muted py-1">
            No topics match "{filter.trim()}".
          </div>
        ) : null}
        {groups.map((g) => (
          <div key={g.category}>
            <div className="text-[10px] uppercase tracking-wide text-muted mb-1">
              {CATEGORY_LABEL[g.category] ?? g.category}{" "}
              <span className="normal-case tracking-normal">
                · {g.topics.length}
              </span>
            </div>
            <ul className="grid gap-0.5">
              {g.topics.map((t) => (
                <li key={t.name} className="flex items-center gap-2 min-w-0">
                  <input
                    type="checkbox"
                    className="accent-accent shrink-0"
                    checked={checked?.has(t.name) ?? false}
                    onChange={() => toggle(t.name)}
                    aria-label={`Include ${t.name} in the generated config`}
                  />
                  <span
                    className="font-mono text-xs truncate"
                    title={t.reason ?? t.name}
                  >
                    {t.name}
                  </span>
                  <span className="text-[10px] text-muted truncate hidden sm:inline">
                    {t.type || "type unknown"}
                  </span>
                  <span className="ml-auto flex items-center gap-1 shrink-0">
                    {captured.has(t.name) ? (
                      <span
                        className={`${BADGE_BASE} bg-green-500/20 text-green-400 border-green-500/40`}
                        title="This topic was present in the robot's most recent capture."
                      >
                        in last capture
                      </span>
                    ) : null}
                    {!t.resolvable ? (
                      <span
                        className={`${BADGE_BASE} bg-red-500/20 text-red-400 border-red-500/40`}
                        title={`The message package for ${t.type || "this type"} is not built/sourced on this robot — capture would silently skip this topic. Build the workspace or set ros_setup_files.`}
                      >
                        type not built
                      </span>
                    ) : null}
                    {t.publishers === 0 ? (
                      <span
                        className={`${BADGE_BASE} bg-yellow-500/20 text-yellow-400 border-yellow-500/40`}
                        title="Advertised but nothing is publishing — possibly a ghost of a dead node, or the echo of a capture subscription."
                      >
                        no publishers
                      </span>
                    ) : null}
                    {t.large ? (
                      <span
                        className={`${BADGE_BASE} bg-blue-500/20 text-blue-400 border-blue-500/40`}
                        title="High-rate / large messages — capturing this grows the MCAP quickly."
                      >
                        large
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2 flex-wrap border-t border-border pt-2">
        <Button
          variant="ghost"
          className="text-xs px-2 py-1"
          onClick={onCopyConfig}
          disabled={effectiveChecked.length === 0}
        >
          {copied ? "Copied ✓" : `Copy config YAML (${effectiveChecked.length})`}
        </Button>
        <span className="text-[10px] text-muted">
          The hub never writes to robots — paste over the topics: block in{" "}
          <span className="font-mono">/etc/missiondebug/config.yaml</span> on
          the robot, then restart the agent.
        </span>
      </div>
    </div>
  );
}
