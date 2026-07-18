import { useMemo, useState } from "react";
import { usePlayback } from "../../stores/playback";
import { nearestByTime } from "../../hooks/useNearestByTime";
import type { OtherTrack } from "../../hooks/useMcapLoader";

/**
 * v2 P1.7.5 — JSON message inspector for arbitrary ROS topics.
 *
 * Shows the decoded message nearest the playhead as a collapsible
 * tree. For topics where the auto-discovered scalar field is too
 * shallow (state machines, custom messages with many fields), this
 * is the way the engineer sees what's actually inside the message at
 * a given moment.
 *
 * Pattern borrowed from ros2_medkit's data panel + JsonFormViewer —
 * same React + Tailwind stack — but synced to the playback time
 * rather than live.
 */

type Props = {
  tracks: OtherTrack[];
};

export function TrackJsonInspector({ tracks }: Props) {
  // Sort topics by message count desc — most-active first.
  const sortedTracks = useMemo(
    () =>
      [...tracks].sort((a, b) => b.messages.length - a.messages.length),
    [tracks],
  );

  // Selected topic (default to the most-active one).
  const [selected, setSelected] = useState<string>(
    () => sortedTracks[0]?.topic ?? "",
  );
  const track = sortedTracks.find((t) => t.topic === selected) ?? sortedTracks[0];

  const startNs = usePlayback((s) => s.startNs);
  const currentTimeNs = usePlayback((s) => s.currentTimeNs);

  const current = track
    ? nearestByTime(track.messages, startNs, currentTimeNs)
    : null;

  if (sortedTracks.length === 0) return null;

  return (
    <div className="bg-panel border border-border rounded p-3 grid gap-2">
      <div className="flex items-baseline justify-between">
        <div className="text-sm">Inspector</div>
        <select
          value={track?.topic ?? ""}
          onChange={(e) => setSelected(e.target.value)}
          className="bg-bg border border-border rounded px-2 py-0.5 text-xs font-mono outline-none focus:border-accent max-w-[60%] truncate"
        >
          {sortedTracks.map((t) => (
            <option key={t.topic} value={t.topic} className="bg-panel">
              {t.topic} ({t.messages.length})
            </option>
          ))}
        </select>
      </div>
      {current === null ? (
        <div className="text-xs text-muted">No message at this time.</div>
      ) : (
        <div className="font-mono text-xs max-h-80 overflow-y-auto pr-1">
          <div className="text-muted mb-1">
            t = {(Number(current.timeNs - startNs) / 1e9).toFixed(3)}s
          </div>
          <TreeNode value={current.decoded} depth={0} keyName="" />
        </div>
      )}
    </div>
  );
}

interface TreeNodeProps {
  value: unknown;
  depth: number;
  keyName: string;
}

function TreeNode({ value, depth, keyName }: TreeNodeProps) {
  // Long primitive arrays (covariance matrices, byte blobs) start
  // collapsed regardless of depth — 27 rows of zeros is what used to
  // make this panel eat screens of vertical space.
  const arr = Array.isArray(value) ? (value as unknown[]) : null;
  const longArray = !!arr && arr.length > 8;
  const [collapsed, setCollapsed] = useState(depth >= 2 || longArray);

  if (value === null || value === undefined) {
    return <LeafRow keyName={keyName} display="null" muted />;
  }

  const t = typeof value;
  if (t === "number" || t === "bigint") {
    const num = t === "bigint" ? (value as bigint).toString() : String(value);
    return <LeafRow keyName={keyName} display={num} accent />;
  }
  if (t === "string") {
    const s = value as string;
    return <LeafRow keyName={keyName} display={`"${s}"`} />;
  }
  if (t === "boolean") {
    return (
      <LeafRow keyName={keyName} display={String(value)} accent />
    );
  }

  // Array or object
  const entries: [string, unknown][] = Array.isArray(value)
    ? (value as unknown[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);

  const isEmpty = entries.length === 0;
  const opener = Array.isArray(value) ? "[" : "{";
  const closer = Array.isArray(value) ? "]" : "}";

  // Compact display for collapsed primitive arrays: "[0 × 9]" when
  // uniform, "[9 values]" otherwise.
  let collapsedLabel = entries.length > 0 ? `${entries.length}…` : "";
  if (arr && arr.length > 0) {
    const primitive = arr.every((v) =>
      ["number", "bigint", "boolean", "string"].includes(typeof v),
    );
    if (primitive) {
      const first = arr[0];
      collapsedLabel = arr.every((v) => v === first)
        ? `${String(first)} × ${arr.length}`
        : `${arr.length} values`;
    }
  }

  return (
    <div className="grid gap-0.5">
      <div
        className={`flex items-baseline gap-1 ${isEmpty ? "" : "cursor-pointer hover:text-text"}`}
        onClick={isEmpty ? undefined : () => setCollapsed((c) => !c)}
      >
        {!isEmpty ? (
          <span className="text-muted w-3 inline-block">
            {collapsed ? "▸" : "▾"}
          </span>
        ) : (
          <span className="w-3 inline-block" />
        )}
        {keyName ? <span className="text-muted">{keyName}:</span> : null}
        {isEmpty || collapsed ? (
          <span className="text-muted">
            {opener}
            {collapsedLabel}
            {closer}
          </span>
        ) : (
          <span className="text-muted">{opener}</span>
        )}
      </div>
      {!isEmpty && !collapsed ? (
        <div className="ml-4 grid gap-0.5 border-l border-border pl-2">
          {entries.map(([k, v]) => (
            <TreeNode key={k} value={v} depth={depth + 1} keyName={k} />
          ))}
          <div className="text-muted">{closer}</div>
        </div>
      ) : null}
    </div>
  );
}

function LeafRow({
  keyName,
  display,
  muted,
  accent,
}: {
  keyName: string;
  display: string;
  muted?: boolean;
  accent?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-1">
      <span className="w-3 inline-block" />
      {keyName ? <span className="text-muted">{keyName}:</span> : null}
      <span
        className={
          accent ? "text-accent" : muted ? "text-muted" : "text-text"
        }
      >
        {display}
      </span>
    </div>
  );
}
