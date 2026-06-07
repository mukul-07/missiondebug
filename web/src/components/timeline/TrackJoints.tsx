import { useEffect, useMemo, useRef, useState } from "react";

import { nearestByTime } from "../../hooks/useNearestByTime";
import { usePlayback } from "../../stores/playback";
import type { DecodedJoints } from "../../workers/types";

/**
 * Manipulator per-joint chart. A `sensor_msgs/JointState` carries parallel
 * arrays (`name[]` + `position[]`/`velocity[]`/`effort[]`) that the generic
 * scalar walker skips (arrays need an index). This renders one polyline per
 * joint for the selected field — so an arm's motion is visible in-app
 * instead of falling through to "no chart". Plain 2D canvas (no WebGL cap),
 * same pattern as TrackScalar.
 */

const HEIGHT = 120;
const PADDING = 8;
const COLOR_BG = "#13161b";
const COLOR_CURSOR = "rgba(255,255,255,0.6)";
const COLOR_LABEL = "#7d8590";
const JOINT_COLORS = [
  "#ff5a5f", "#4dabf7", "#51cf66", "#ffd43b", "#cc5de8",
  "#22b8cf", "#ff922b", "#a9e34b", "#f06595", "#3bc9db",
];

type Field = "position" | "velocity" | "effort";
const FIELDS: Field[] = ["position", "velocity", "effort"];

export function TrackJoints({ topic, samples }: { topic: string; samples: DecodedJoints[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const startNs = usePlayback((s) => s.startNs);
  const currentTimeNs = usePlayback((s) => s.currentTimeNs);

  // Joint names: take the longest name array seen (handles position-only msgs).
  const names = useMemo(() => {
    let best: string[] = [];
    for (const s of samples) if (s.names.length > best.length) best = s.names;
    return best;
  }, [samples]);

  // Which of position/velocity/effort actually have data.
  const available = useMemo(
    () => FIELDS.filter((f) => samples.some((s) => s[f].length > 0)),
    [samples],
  );

  const [picked, setPicked] = useState<Field | null>(null);
  const field: Field =
    (picked && available.includes(picked) ? picked : available[0]) ?? "position";

  const jointCount = useMemo(() => {
    let n = names.length;
    for (const s of samples) n = Math.max(n, s[field].length);
    return n;
  }, [samples, names, field]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const cssW = parent.clientWidth;
      const dpr = window.devicePixelRatio || 1;
      if (
        canvas.width !== Math.round(cssW * dpr) ||
        canvas.height !== Math.round(HEIGHT * dpr)
      ) {
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(HEIGHT * dpr);
        canvas.style.width = `${cssW}px`;
        canvas.style.height = `${HEIGHT}px`;
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawJoints(ctx, samples, field, jointCount, cssW, HEIGHT, startNs, currentTimeNs);
    };
    draw();
    const ro = new ResizeObserver(draw);
    if (canvas.parentElement) ro.observe(canvas.parentElement);
    return () => ro.disconnect();
  }, [samples, field, jointCount, currentTimeNs, startNs]);

  if (samples.length === 0 || available.length === 0) return null;

  const current = nearestByTime(samples, startNs, currentTimeNs);

  return (
    <div className="bg-panel border border-border rounded p-2 grid gap-1">
      <div className="flex items-baseline justify-between text-xs gap-2">
        <span className="font-mono text-muted truncate" title={topic}>{topic}</span>
        <div className="flex gap-1">
          {available.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setPicked(f)}
              className={`text-[10px] px-1.5 py-0.5 rounded border ${
                f === field
                  ? "border-accent text-accent"
                  : "border-border text-muted hover:text-text"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>
      <div
        style={{ width: "100%", height: HEIGHT }}
        className="border border-border rounded overflow-hidden"
      >
        <canvas ref={canvasRef} style={{ display: "block" }} />
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]">
        {Array.from({ length: jointCount }, (_, j) => {
          const v = current ? current[field][j] : undefined;
          return (
            <span key={j} className="font-mono inline-flex items-center gap-1">
              <span
                style={{ background: JOINT_COLORS[j % JOINT_COLORS.length] }}
                className="inline-block w-2 h-2 rounded-sm"
              />
              <span className="text-muted">{names[j] ?? `joint ${j}`}</span>
              <span>{typeof v === "number" && Number.isFinite(v) ? v.toFixed(3) : "—"}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function drawJoints(
  ctx: CanvasRenderingContext2D,
  samples: DecodedJoints[],
  field: Field,
  jointCount: number,
  w: number,
  h: number,
  startNs: bigint,
  currentTimeNs: bigint,
) {
  ctx.fillStyle = COLOR_BG;
  ctx.fillRect(0, 0, w, h);
  if (samples.length < 2 || jointCount === 0) return;

  const usableW = Math.max(1, w - 2 * PADDING);
  const yTop = 8;
  const yBot = h - 8;

  let minV = Infinity;
  let maxV = -Infinity;
  for (const s of samples) {
    for (let j = 0; j < jointCount; j++) {
      const v = s[field][j];
      if (typeof v === "number" && Number.isFinite(v)) {
        if (v < minV) minV = v;
        if (v > maxV) maxV = v;
      }
    }
  }
  if (!Number.isFinite(minV) || !Number.isFinite(maxV)) return;
  if (minV === maxV) {
    minV -= 1;
    maxV += 1;
  }

  const t0 = samples[0].timeNs;
  const tEnd = samples[samples.length - 1].timeNs;
  const span = tEnd - t0;
  if (span <= 0n) return;

  for (let j = 0; j < jointCount; j++) {
    ctx.strokeStyle = JOINT_COLORS[j % JOINT_COLORS.length];
    ctx.lineWidth = 1;
    ctx.beginPath();
    let started = false;
    for (const s of samples) {
      const v = s[field][j];
      if (typeof v !== "number" || !Number.isFinite(v)) continue;
      const frac = Number(s.timeNs - t0) / Number(span);
      const x = PADDING + frac * usableW;
      const y = yBot - ((v - minV) / (maxV - minV)) * (yBot - yTop);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }

  ctx.fillStyle = COLOR_LABEL;
  ctx.font = "9px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.textBaseline = "top";
  ctx.fillText(maxV.toFixed(2), PADDING + 2, yTop - 2);
  ctx.fillText(minV.toFixed(2), PADDING + 2, yBot - 10);

  const playNs = startNs + currentTimeNs;
  if (playNs >= t0 && playNs <= tEnd) {
    const frac = Number(playNs - t0) / Number(span);
    const x = PADDING + frac * usableW;
    ctx.strokeStyle = COLOR_CURSOR;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, 4);
    ctx.lineTo(x, h - 4);
    ctx.stroke();
  }
}
