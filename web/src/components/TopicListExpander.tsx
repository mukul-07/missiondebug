import { useState } from "react";

/**
 * v2 P1.7.6 — click-to-expand topic list. Replaces the muted
 * "N topics" badge with something that actually surfaces which
 * topics are in the session. Critical at fleet scale where a
 * 70-topic session needs a way to see *what's in there* without
 * downloading the MCAP.
 *
 * Used on both SessionList cards (inline) and SessionDetail.
 */

type Props = {
  topics: string[];
  /** Compact (inline within a list row) vs full (on detail pages). */
  compact?: boolean;
};

export function TopicListExpander({ topics, compact = false }: Props) {
  const [open, setOpen] = useState(false);
  const label = `${topics.length} topic${topics.length === 1 ? "" : "s"}`;

  if (compact) {
    return (
      <span
        onClick={(e) => {
          // Stop the parent Link from navigating when expanding.
          e.preventDefault();
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="px-1.5 py-0.5 rounded border border-border text-muted hover:border-accent cursor-pointer"
        title={open ? "Click to collapse" : "Click to list all topics"}
      >
        {open ? "▾" : "▸"} {label}
        {open ? (
          <span
            className="block mt-1 font-mono text-[10px] leading-snug whitespace-pre-wrap"
            onClick={(e) => e.stopPropagation()}
          >
            {topics.join("\n")}
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <div className="bg-panel border border-border rounded p-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-xs text-muted hover:text-text"
      >
        {open ? "▾" : "▸"} {label}
      </button>
      {open ? (
        <ul className="mt-2 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-x-3 gap-y-0.5 font-mono text-xs">
          {topics.map((t) => (
            <li key={t} className="truncate" title={t}>
              {t}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
