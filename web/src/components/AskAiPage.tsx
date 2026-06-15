/**
 * "Ask AI" page — the natural-language incident agent as a first-class route
 * (/ask), not a modal. A shareable, back-button-able surface: "ask your
 * fleet's incident history in plain English." Wraps IncidentAgentPanel in the
 * standard page shell; the panel itself handles the enabled/disabled/answer
 * states (HR24).
 */

import { IncidentAgentPanel } from "./IncidentAgentPanel";

export function AskAiPage() {
  return (
    <div className="p-4 grid gap-4 max-w-3xl">
      <div>
        <h1 className="text-lg font-semibold flex items-center gap-2">
          <span aria-hidden>✨</span> Ask AI
        </h1>
        <p className="text-sm text-muted mt-1">
          Ask your fleet's incident history in plain English. Answers are
          grounded in your captured incidents and cite the exact sessions they
          came from.
        </p>
      </div>
      <IncidentAgentPanel />
    </div>
  );
}
