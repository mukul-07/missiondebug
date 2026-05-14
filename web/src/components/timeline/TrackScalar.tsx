import { useEffect, useRef } from "react";
import { usePlayback } from "../../stores/playback";
import { nearestByTime } from "../../hooks/useNearestByTime";
import type { ScalarTrack } from "../../hooks/useMcapLoader";

/**
 * v2 P1.7.4 — Auto-rendered scalar chart for any "other" topic the
 * MCAP worker successfully extracted a numeric field from.
 *
 * Uses a plain 2D canvas (not WebGL/PixiJS) because a fleet robot has
 * 30+ topics and browsers cap WebGL contexts at ~16 per page. 2D
 * canvases have effectively no limit, and these polylines don't need
 * GPU acceleration.
 */

type Props = {
  track: ScalarTrack;
};

const HEIGHT = 80;
const PADDING = 8;
const COLOR_LINE = "#ff5a5f";
const COLOR_LABEL = "#7d8590";
const COLOR_CURSOR = "rgba(255, 255, 255, 0.6)";
const COLOR_BG = "#13161b";

export function TrackScalar({ track }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const startNs = usePlayback((s) => s.startNs);
  const currentTimeNs = usePlayback((s) => s.currentTimeNs);
  const durationNs = usePlayback((s) => s.durationNs);

  const current = nearestByTime(track.samples, startNs, currentTimeNs);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const cssW = parent.clientWidth;
      const cssH = HEIGHT;
      const dpr = window.devicePixelRatio || 1;
      // Match canvas internal resolution to CSS size × DPR.
      if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssH * dpr);
        canvas.style.width = `${cssW}px`;
        canvas.style.height = `${cssH}px`;
      }
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawChart(ctx, track, cssW, cssH, startNs, currentTimeNs);
    };
    draw();
    // Redraw on container resize (e.g. theme/layout change).
    const ro = new ResizeObserver(draw);
    if (canvas.parentElement) ro.observe(canvas.parentElement);
    return () => ro.disconnect();
  }, [track.samples, track.topic, currentTimeNs, startNs]);

  if (track.samples.length === 0) return null;

  return (
    <div className="bg-panel border border-border rounded p-2 grid gap-1">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-mono text-muted truncate" title={track.topic}>
          {track.topic}
        </span>
        <span
          className="font-mono text-muted text-[10px] truncate"
          title={`field: ${track.fieldPath}`}
        >
          .{track.fieldPath}
        </span>
      </div>
      <div
        style={{ width: "100%", height: HEIGHT }}
        className="border border-border rounded overflow-hidden"
      >
        <canvas ref={canvasRef} style={{ display: "block" }} />
      </div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted">
          n={track.samples.length}{" "}
          {durationNs > 0n ? `· ${(track.samples.length / (Number(durationNs) / 1e9)).toFixed(1)} Hz` : ""}
        </span>
        <span className="font-mono">
          {current !== null ? current.value.toFixed(3) : "—"}
        </span>
      </div>
    </div>
  );
}

function drawChart(
  ctx: CanvasRenderingContext2D,
  track: ScalarTrack,
  w: number,
  h: number,
  startNs: bigint,
  currentTimeNs: bigint,
) {
  ctx.fillStyle = COLOR_BG;
  ctx.fillRect(0, 0, w, h);

  const { samples } = track;
  if (samples.length < 2) return;
  const usableW = Math.max(1, w - 2 * PADDING);
  const yTop = 8;
  const yBot = h - 8;

  let minV = Infinity;
  let maxV = -Infinity;
  for (const s of samples) {
    if (s.value < minV) minV = s.value;
    if (s.value > maxV) maxV = s.value;
  }
  if (minV === maxV) {
    minV -= 1;
    maxV += 1;
  }
  const t0 = samples[0].timeNs;
  const tEnd = samples[samples.length - 1].timeNs;
  const span = tEnd - t0;
  if (span <= 0n) return;

  // Polyline.
  ctx.strokeStyle = COLOR_LINE;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    const frac = Number(s.timeNs - t0) / Number(span);
    const x = PADDING + frac * usableW;
    const y = yBot - ((s.value - minV) / (maxV - minV)) * (yBot - yTop);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Min/max labels (top-left of plot area).
  ctx.fillStyle = COLOR_LABEL;
  ctx.font = "9px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.textBaseline = "top";
  ctx.fillText(maxV.toFixed(2), PADDING + 2, yTop - 2);
  ctx.fillText(minV.toFixed(2), PADDING + 2, yBot - 10);

  // Playhead cursor.
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
