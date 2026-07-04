/**
 * First-run activation checklist — the landing page owns the journey from
 * empty hub to first capture instead of dead-ending in an empty state.
 *
 * Renders only while the hub has zero agents or zero sessions (and never
 * again once dismissed). Steps 3 and 4 verify THEMSELVES from live data:
 * the hub knows when the first heartbeat and the first capture arrive, so
 * the operator gets a green tick instead of wondering whether their config
 * took. The hub URL in step 2 is auto-filled from this page's own origin —
 * the one address that demonstrably reaches this hub from the network.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { copyText } from "../lib/clipboard";

const LS_KEY = "md:firstrun"; // null | "active" | "done"

const INSTALL_CMD =
  "curl -fsSL https://raw.githubusercontent.com/mukul-07/missiondebug/main/scripts/install.sh \\\n  | sudo bash -s -- --hub-url " +
  window.location.origin;

const CONFIG_SNIPPET = `# /etc/missiondebug/config.yaml on the robot
http_host: "0.0.0.0"   # so this hub can reach the agent back
hub:
  url: "${window.location.origin}"
  agent_url: "http://<robot-ip>:7000"`;

const SAVE_CMD = "curl -X POST http://<robot-ip>:7000/sessions/save";

function CopyBlock({ text, promptLabel }: { text: string; promptLabel: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative bg-bg border border-border rounded px-3 py-2 font-mono text-[11px] whitespace-pre overflow-x-auto text-text/85">
      <button
        type="button"
        onClick={async () => {
          if (await copyText(text, promptLabel)) {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          }
        }}
        className="absolute top-1.5 right-1.5 text-[10px] px-2 py-0.5 rounded border border-border bg-panel text-muted hover:text-accent hover:border-accent"
      >
        {copied ? "copied ✓" : "copy"}
      </button>
      {text}
    </div>
  );
}

function Tick({ state, n }: { state: "todo" | "wait" | "done"; n: number }) {
  if (state === "done") {
    return (
      <span className="w-[18px] h-[18px] rounded-full border border-green-500/40 bg-green-500/20 text-green-400 flex items-center justify-center text-[11px] mt-0.5 shrink-0">
        ✓
      </span>
    );
  }
  return (
    <span
      className={`w-[18px] h-[18px] rounded-full border flex items-center justify-center text-[11px] mt-0.5 shrink-0 ${
        state === "wait" ? "border-yellow-400 text-yellow-400 animate-pulse" : "border-border text-muted"
      }`}
    >
      {n}
    </span>
  );
}

export function FirstRunChecklist({
  agentCount,
  sessionCount,
  firstRobotId,
}: {
  agentCount: number;
  sessionCount: number;
  firstRobotId: string | null;
}) {
  const [stage, setStage] = useState<string | null>(() => localStorage.getItem(LS_KEY));
  const incomplete = agentCount === 0 || sessionCount === 0;

  // Mark the checklist "active" the first time it renders on an empty hub,
  // so the completion card knows to show once (and only for hubs that
  // actually went through the checklist — a pre-populated hub never sees it).
  useEffect(() => {
    if (incomplete && stage === null) {
      localStorage.setItem(LS_KEY, "active");
      setStage("active");
    }
  }, [incomplete, stage]);

  if (stage === "done") return null;

  // Everything arrived: show the send-off once, then retire forever.
  if (!incomplete) {
    if (stage !== "active") return null;
    return (
      <div className="bg-panel border border-green-500/40 rounded p-3 grid gap-1.5">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="text-green-400 text-sm font-semibold">✓ Setup complete</span>
          <span className="text-xs text-muted">
            first capture{firstRobotId ? <> from <span className="font-mono">{firstRobotId}</span></> : null} is below
          </span>
          <button
            type="button"
            onClick={() => {
              localStorage.setItem(LS_KEY, "done");
              setStage("done");
            }}
            className="ml-auto text-xs px-2 py-1 rounded border border-border hover:border-accent hover:text-accent"
          >
            Dismiss
          </button>
        </div>
        <p className="text-xs text-muted m-0">
          Next: browse the robot's live topics on the{" "}
          <Link to="/fleet/agents" className="text-accent hover:underline">Agents page</Link>{" "}
          to tune what gets captured, add anomaly rules so captures trigger themselves, and
          when an incident repeats, the{" "}
          <Link to="/fleet/incidents" className="text-accent hover:underline">fleet dashboard</Link>{" "}
          starts earning its keep.
        </p>
      </div>
    );
  }

  const agentDone = agentCount > 0;

  return (
    <div className="bg-panel border border-border rounded p-3 grid gap-1">
      <div className="flex items-baseline gap-3 flex-wrap mb-1">
        <span className="text-sm font-semibold">Get your first capture</span>
        <span className="text-[11px] text-muted">
          {(agentDone ? 3 : 0) + (sessionCount > 0 ? 1 : 0)} of 4 · this card retires itself when you're done
        </span>
      </div>

      <div className="grid grid-cols-[22px_1fr] gap-x-2.5 py-2">
        <Tick state="todo" n={1} />
        <div className="grid gap-1.5 min-w-0">
          <div className="text-[13px] font-semibold">Install the agent on a robot</div>
          <p className="text-xs text-muted m-0">
            Ubuntu 22.04 / 24.04 with ROS 2. One command — it adds the apt repo, installs, and
            points the agent at this hub:
          </p>
          <CopyBlock text={INSTALL_CMD} promptLabel="Copy the install command:" />
        </div>
      </div>

      <div className="grid grid-cols-[22px_1fr] gap-x-2.5 py-2 border-t border-border">
        <Tick state="todo" n={2} />
        <div className="grid gap-1.5 min-w-0">
          <div className="text-[13px] font-semibold">…or wire an existing agent to this hub</div>
          <p className="text-xs text-muted m-0">
            Already installed? Add this to the robot's config, then{" "}
            <span className="font-mono">sudo systemctl restart missiondebug-agent</span>:
          </p>
          <CopyBlock text={CONFIG_SNIPPET} promptLabel="Copy the config snippet:" />
        </div>
      </div>

      <div className="grid grid-cols-[22px_1fr] gap-x-2.5 py-2 border-t border-border">
        <Tick state={agentDone ? "done" : "wait"} n={3} />
        <div className="min-w-0">
          <div className={`text-[13px] font-semibold ${agentDone ? "text-muted" : ""}`}>Watch it appear</div>
          {agentDone ? (
            <p className="text-xs m-0">
              <span className="text-green-400">
                ● <span className="font-mono">{firstRobotId ?? "agent"}</span> reporting
              </span>{" "}
              <Link to="/fleet/agents" className="text-accent hover:underline">
                see it on Fleet health →
              </Link>
            </p>
          ) : (
            <p className="text-xs text-muted m-0">
              Checks itself off at the first heartbeat — usually within 60 seconds of the agent
              starting. <Link to="/fleet/agents" className="text-accent hover:underline">Fleet health →</Link>
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-[22px_1fr] gap-x-2.5 py-2 border-t border-border">
        <Tick state={sessionCount > 0 ? "done" : agentDone ? "wait" : "todo"} n={4} />
        <div className="grid gap-1.5 min-w-0">
          <div className="text-[13px] font-semibold">Trigger a test capture</div>
          <p className="text-xs text-muted m-0">From the robot (or anywhere that can reach it):</p>
          <CopyBlock text={SAVE_CMD} promptLabel="Copy the save command:" />
        </div>
      </div>
    </div>
  );
}
