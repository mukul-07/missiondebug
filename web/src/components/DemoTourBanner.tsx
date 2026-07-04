/**
 * Demo-tour strip — the in-product version of docs/DEMO-SCRIPT.md.
 *
 * Shows only when the hub reports demo_tour (the demos image sets
 * MD_DEMO_TOUR=1; real deployments never do), and only until dismissed.
 * Points a self-serve evaluator at the three stops that tell the story.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getHubMeta } from "../api/meta";

const LS_KEY = "md:demo-tour-dismissed";

export function DemoTourBanner({ firstSessionId }: { firstSessionId: string | null }) {
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(LS_KEY) === "1");
  const { data } = useQuery({
    queryKey: ["hub-meta"],
    queryFn: getHubMeta,
    staleTime: Infinity,
  });

  if (dismissed || !data?.demo_tour) return null;

  return (
    <div className="flex items-center gap-2.5 flex-wrap text-xs bg-panel border border-border border-l-2 border-l-accent rounded px-3 py-2">
      <span>
        👋 <span className="font-semibold">Seeded demo data.</span> The 3-minute tour:
      </span>
      {firstSessionId ? (
        <Link to={`/sessions/${encodeURIComponent(firstSessionId)}`} className="text-accent hover:underline">
          ① a captured incident
        </Link>
      ) : (
        <span className="text-muted">① a captured incident</span>
      )}
      <span className="text-muted">·</span>
      <Link to="/fleet/incidents" className="text-accent hover:underline">
        ② the fleet dashboard
      </Link>
      <span className="text-muted">·</span>
      <Link to="/ask" className="text-accent hover:underline">
        ③ ask the incident history
      </Link>
      <button
        type="button"
        aria-label="Dismiss the tour"
        onClick={() => {
          localStorage.setItem(LS_KEY, "1");
          setDismissed(true);
        }}
        className="ml-auto text-muted hover:text-text"
      >
        ✕
      </button>
    </div>
  );
}
