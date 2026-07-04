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

  const stops = [
    {
      n: "①",
      title: "Replay an incident",
      desc: "the 60 seconds before a failure, scrubbable, with “has this happened before?”",
      to: firstSessionId ? `/sessions/${encodeURIComponent(firstSessionId)}` : null,
    },
    {
      n: "②",
      title: "See the fleet dashboard",
      desc: "recurrence, MTTR, and what repeat incidents cost this fleet",
      to: "/fleet/incidents",
    },
    {
      n: "③",
      title: "Ask AI",
      desc: "plain-English questions over the whole incident history",
      to: "/ask",
    },
  ];

  return (
    <div className="bg-panel border border-border border-l-2 border-l-accent rounded px-3 py-2.5 grid gap-2">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-sm">
          👋 <span className="font-semibold">This hub is pre-loaded with demo data</span>
        </span>
        <span className="text-xs text-muted">
          — a week of incidents from a small warehouse fleet. Three stops show what it does:
        </span>
        <button
          type="button"
          aria-label="Dismiss the tour"
          onClick={() => {
            localStorage.setItem(LS_KEY, "1");
            setDismissed(true);
          }}
          className="ml-auto text-xs text-muted hover:text-text"
        >
          ✕ dismiss
        </button>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        {stops.map((s) =>
          s.to ? (
            <Link
              key={s.n}
              to={s.to}
              className="block rounded border border-border px-2.5 py-2 hover:border-accent group"
            >
              <div className="text-xs">
                <span className="text-accent">{s.n}</span>{" "}
                <span className="text-accent group-hover:underline">{s.title}</span>
              </div>
              <div className="text-[11px] text-muted mt-0.5">{s.desc}</div>
            </Link>
          ) : (
            <div key={s.n} className="rounded border border-border px-2.5 py-2 opacity-60">
              <div className="text-xs">{s.n} {s.title}</div>
              <div className="text-[11px] text-muted mt-0.5">{s.desc}</div>
            </div>
          ),
        )}
      </div>
    </div>
  );
}
