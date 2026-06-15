import { Command } from "cmdk";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listAgents } from "../api/agents";
import { listSessions } from "../api/sessions";

/**
 * Quick-jump palette. cmd+K (or ctrl+K) toggles it open.
 *
 * Indexes:
 *   - Every session (id, label, robot, subsystem)
 *   - Every reporting agent (robot_id, subsystem)
 *   - Every distinct subsystem
 *
 * Tab/arrows navigate, Enter activates. Esc closes.
 *
 * Pattern borrowed from ros2_medkit's SearchCommand component
 * (Apache 2.0, same React + Tailwind stack). Implementation is
 * our own using the `cmdk` library directly.
 */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  // Toggle on cmd+K / ctrl+K.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Lazy-load data only while open — no point thrashing the API when
  // the palette is closed.
  const { data: sessionsResp } = useQuery({
    queryKey: ["palette-sessions"],
    queryFn: () => listSessions(),
    enabled: open,
    staleTime: 10_000,
  });
  const { data: agents = [] } = useQuery({
    queryKey: ["palette-agents"],
    queryFn: listAgents,
    enabled: open,
    staleTime: 30_000,
  });

  const sessions = sessionsResp?.sessions ?? [];

  const subsystems = useMemo(() => {
    const set = new Set<string>();
    for (const a of agents) if (a.subsystem) set.add(a.subsystem);
    for (const s of sessions) if (s.subsystem) set.add(s.subsystem);
    return Array.from(set).sort();
  }, [agents, sessions]);

  const closeAnd = (fn: () => void) => () => {
    setOpen(false);
    fn();
  };

  if (!open) return null;

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command Menu"
      className="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/40"
    >
      <div
        className="w-[640px] max-w-[92vw] bg-panel border border-border rounded shadow-xl text-text"
        onClick={(e) => e.stopPropagation()}
      >
        <Command shouldFilter>
          <Command.Input
            placeholder="Jump to a session, robot, or subsystem…"
            className="w-full bg-panel border-b border-border px-3 py-3 outline-none text-sm placeholder:text-muted"
          />
          <Command.List className="max-h-[60vh] overflow-y-auto py-1">
            <Command.Empty className="px-3 py-4 text-sm text-muted">
              No matches.
            </Command.Empty>

            <Command.Group heading="Pages" className="px-1 py-1 text-xs text-muted">
              <Command.Item
                value="pages fleet incidents dashboard kpi mttr resolution recurrence"
                onSelect={closeAnd(() => navigate("/fleet/incidents"))}
                className="px-2 py-1.5 rounded text-sm text-text hover:bg-bg cursor-pointer data-[selected=true]:bg-bg"
              >
                <div>Fleet incidents</div>
                <div className="text-xs text-muted">
                  MTTR, resolution rate, top patterns · /fleet/incidents
                </div>
              </Command.Item>
              <Command.Item
                value="pages fleet health agents operational"
                onSelect={closeAnd(() => navigate("/fleet/agents"))}
                className="px-2 py-1.5 rounded text-sm text-text hover:bg-bg cursor-pointer data-[selected=true]:bg-bg"
              >
                <div>Fleet agents</div>
                <div className="text-xs text-muted">
                  Which agents are reporting · /fleet/agents
                </div>
              </Command.Item>
              <Command.Item
                value="pages sessions list home"
                onSelect={closeAnd(() => navigate("/"))}
                className="px-2 py-1.5 rounded text-sm text-text hover:bg-bg cursor-pointer data-[selected=true]:bg-bg"
              >
                <div>Sessions</div>
                <div className="text-xs text-muted">All captured sessions · /</div>
              </Command.Item>
              <Command.Item
                value="pages ask ai incident agent natural language question history"
                onSelect={closeAnd(() => navigate("/ask"))}
                className="px-2 py-1.5 rounded text-sm text-text hover:bg-bg cursor-pointer data-[selected=true]:bg-bg"
              >
                <div>Ask AI</div>
                <div className="text-xs text-muted">
                  Ask the incident history in plain English · /ask
                </div>
              </Command.Item>
            </Command.Group>

            {sessions.length > 0 ? (
              <Command.Group heading="Sessions" className="px-1 py-1 text-xs text-muted">
                {sessions.slice(0, 50).map((s) => (
                  <Command.Item
                    key={`s-${s.id}`}
                    value={`session ${s.id} ${s.robot_id} ${s.label ?? ""} ${s.subsystem ?? ""}`}
                    onSelect={closeAnd(() =>
                      navigate(`/sessions/${encodeURIComponent(s.id)}`),
                    )}
                    className="px-2 py-1.5 rounded text-sm text-text hover:bg-bg cursor-pointer data-[selected=true]:bg-bg"
                  >
                    <div className="font-mono">{s.id}</div>
                    <div className="text-xs text-muted">
                      {s.robot_id}
                      {s.subsystem ? ` · ${s.subsystem}` : ""}
                      {s.label ? ` · ${s.label}` : ""}
                    </div>
                  </Command.Item>
                ))}
              </Command.Group>
            ) : null}

            {agents.length > 0 ? (
              <Command.Group heading="Robots" className="px-1 py-1 text-xs text-muted">
                {agents.map((a) => (
                  <Command.Item
                    key={`a-${a.robot_id}`}
                    value={`robot ${a.robot_id} ${a.subsystem ?? ""}`}
                    onSelect={closeAnd(() =>
                      navigate(`/?robot=${encodeURIComponent(a.robot_id)}`),
                    )}
                    className="px-2 py-1.5 rounded text-sm text-text hover:bg-bg cursor-pointer data-[selected=true]:bg-bg"
                  >
                    <div className="font-mono">{a.robot_id}</div>
                    <div className="text-xs text-muted">
                      {a.subsystem ?? "no subsystem"}
                      {a.last_heartbeat
                        ? ` · last seen ${new Date(
                            a.last_heartbeat,
                          ).toLocaleTimeString()}`
                        : " · never seen"}
                    </div>
                  </Command.Item>
                ))}
              </Command.Group>
            ) : null}

            {subsystems.length > 0 ? (
              <Command.Group heading="Subsystems" className="px-1 py-1 text-xs text-muted">
                {subsystems.map((s) => (
                  <Command.Item
                    key={`sub-${s}`}
                    value={`subsystem ${s}`}
                    onSelect={closeAnd(() =>
                      navigate(`/?subsystem=${encodeURIComponent(s)}`),
                    )}
                    className="px-2 py-1.5 rounded text-sm text-text hover:bg-bg cursor-pointer data-[selected=true]:bg-bg"
                  >
                    <span className="font-mono">{s}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            ) : null}
          </Command.List>
          <div className="border-t border-border px-3 py-1.5 text-[10px] text-muted flex justify-between">
            <span>↑↓ to navigate · Enter to open · Esc to close</span>
            <span>⌘K</span>
          </div>
        </Command>
      </div>
    </Command.Dialog>
  );
}
