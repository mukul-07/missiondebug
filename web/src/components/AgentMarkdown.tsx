/**
 * Minimal markdown renderer for the incident agent's answers — no dependency.
 *
 * The LLM returns light markdown (bold, italics, inline code, headings,
 * bullet/numbered lists). We render those, and additionally turn any cited
 * session id that appears in the prose into a click-through link, so the
 * incidents the answer names are reachable inline (not just via the chips).
 *
 * Deliberately line-based (not a full parser): it renders each line by its
 * marker + leading indent, which handles the LLM's nested "1. … / - …"
 * structure visually without a block tree. Good enough for constrained
 * assistant output; swap for react-markdown if answers ever get richer.
 */

import { type ReactNode } from "react";
import { Link } from "react-router-dom";

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function renderInline(text: string, citations: string[], keyBase = 0): ReactNode[] {
  const cites = [...citations].sort((a, b) => b.length - a.length).map(escapeRegex);
  const pattern = new RegExp(
    "(\\*\\*[^*]+\\*\\*)|(`[^`]+`)|(\\*[^*]+\\*)" +
      (cites.length ? "|(" + cites.join("|") + ")" : ""),
    "g",
  );
  const out: ReactNode[] = [];
  let last = 0;
  let key = keyBase;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (m[1]) {
      out.push(<strong key={key++}>{renderInline(tok.slice(2, -2), citations, key * 100)}</strong>);
    } else if (m[2]) {
      out.push(
        <code key={key++} className="font-mono text-xs bg-bg px-1 py-0.5 rounded">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (m[3]) {
      out.push(<em key={key++}>{renderInline(tok.slice(1, -1), citations, key * 100)}</em>);
    } else {
      out.push(
        <Link
          key={key++}
          to={`/sessions/${encodeURIComponent(tok)}`}
          className="font-mono text-accent hover:underline"
        >
          {tok}
        </Link>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function AgentMarkdown({
  text,
  citations,
}: {
  text: string;
  citations: string[];
}) {
  const lines = text.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];

  lines.forEach((raw, idx) => {
    const indent = raw.match(/^\s*/)?.[0].length ?? 0;
    const line = raw.trim();
    if (line === "") return; // gaps come from the grid gap

    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      out.push(
        <div key={idx} className="font-semibold text-text mt-1">
          {renderInline(h[2], citations)}
        </div>,
      );
      return;
    }

    const ul = line.match(/^[-*]\s+(.*)$/);
    if (ul) {
      out.push(
        <div key={idx} className="flex gap-2" style={{ marginLeft: 8 + indent * 6 }}>
          <span className="text-muted select-none">•</span>
          <span>{renderInline(ul[1], citations)}</span>
        </div>,
      );
      return;
    }

    const ol = line.match(/^(\d+)\.\s+(.*)$/);
    if (ol) {
      out.push(
        <div key={idx} className="flex gap-2" style={{ marginLeft: indent * 6 }}>
          <span className="text-muted select-none font-mono">{ol[1]}.</span>
          <span>{renderInline(ol[2], citations)}</span>
        </div>,
      );
      return;
    }

    out.push(<div key={idx}>{renderInline(line, citations)}</div>);
  });

  return <div className="text-sm text-text leading-relaxed grid gap-1.5">{out}</div>;
}
