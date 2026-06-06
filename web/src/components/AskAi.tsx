/**
 * Global "Ask AI" entry point in the top nav — opens the incident agent in a
 * modal so it's reachable from any page (Sessions, Incidents, Agents, a
 * session detail), not just the fleet dashboard. Reuses IncidentAgentPanel.
 */

import { useEffect, useState } from "react";
import { IncidentAgentPanel } from "./IncidentAgentPanel";

export function AskAi() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Ask the incident history in plain English"
        className="text-xs flex items-center gap-1 rounded border border-accent/60 text-accent px-2 py-0.5 hover:bg-accent/10"
      >
        <span aria-hidden>✨</span> Ask AI
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-4 pt-16 sm:pt-24"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-end mb-1">
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-xs text-muted hover:text-text"
                aria-label="Close"
              >
                ✕ esc
              </button>
            </div>
            <IncidentAgentPanel />
          </div>
        </div>
      ) : null}
    </>
  );
}
