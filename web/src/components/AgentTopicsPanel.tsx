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
import {
  type AgentTopic,
  type AgentTopics,
  AgentTopicsError,
  getAgentTopics,
} from "../api/agents";
import { copyText } from "../lib/clipboard";
import { Button } from "./ui/Button";
import { Skeleton } from "./ui/Skeleton";

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
  const lines = ["topics:"];
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

  // Capture-selection checkboxes: pre-check recommended topics whose type
  // actually resolves on the robot (a recommended-but-unbuilt topic would
  // capture nothing). Initialized once from the first SETTLED scan — a
  // partial scan would pre-check only the topics it happened to see, and
  // the ones arriving at settle would stay unchecked. Rescans after init
  // keep the user's edits.
  const [checked, setChecked] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (view?.settled && checked === null) {
      setChecked(
        new Set(
          view.topics
            .filter((t) => t.recommended && t.resolvable)
            .map((t) => t.name),
        ),
      );
    }
  }, [view, checked]);

  const groups = useMemo(() => {
    if (!view) return [];
    const byCat = new Map<string, AgentTopic[]>();
    for (const t of view.topics) {
      const cat = CATEGORY_ORDER.includes(t.category) ? t.category : "other";
      const list = byCat.get(cat) ?? [];
      list.push(t);
      byCat.set(cat, list);
    }
    return CATEGORY_ORDER.filter((c) => byCat.has(c)).map((c) => ({
      category: c,
      topics: byCat.get(c) as AgentTopic[],
    }));
  }, [view]);

  const [copied, setCopied] = useState(false);
  const onCopyConfig = async () => {
    if (!view || !checked || checked.size === 0) return;
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
    } else {
      message = String(err);
    }
    const retriable =
      !(err instanceof AgentTopicsError) || err.status === 502;
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
          {view.topics.length} visible · {recommendedCount} recommended
        </span>
        <Button
          variant="ghost"
          className="text-xs px-2 py-1 ml-auto"
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

      <div className="grid gap-2 max-h-80 overflow-y-auto pr-1">
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
          disabled={!checked || checked.size === 0}
        >
          {copied ? "Copied ✓" : `Copy config YAML (${checked?.size ?? 0})`}
        </Button>
        <span className="text-[10px] text-muted">
          The hub never writes to robots — paste into the agent's config.yaml.
        </span>
      </div>
    </div>
  );
}
