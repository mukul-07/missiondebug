import { useEffect, useRef } from "react";
import { Application, Container, Graphics, Text } from "pixi.js";
import { usePlayback } from "../../stores/playback";
import { nearestByTime } from "../../hooks/useNearestByTime";
import type { ScalarTrack } from "../../hooks/useMcapLoader";

/**
 * v2 P1.7.4 — Auto-rendered scalar chart for any "other" topic the
 * MCAP worker successfully extracted a numeric field from.
 *
 * One TrackScalar = one topic. Renders the picked numeric field as a
 * line over time, plus the current value at the playhead. Min/max
 * labels on the right edge.
 *
 * Picked-field discovery happens in the worker (pickScalarField in
 * mcap-decoder.ts). Customers can later override the picked field per
 * topic via agent config — for now, first numeric leaf wins.
 */

type Props = {
  track: ScalarTrack;
};

const HEIGHT = 80;
const PADDING = 8;

export function TrackScalar({ track }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const cursorRef = useRef<Graphics | null>(null);

  const startNs = usePlayback((s) => s.startNs);
  const currentTimeNs = usePlayback((s) => s.currentTimeNs);
  const durationNs = usePlayback((s) => s.durationNs);

  // For the "current value at playhead" readout.
  const current = nearestByTime(track.samples, startNs, currentTimeNs);

  // INIT: pixi app + chart graphics. Re-runs only when the topic/
  // samples set changes — typical case is once per session load.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let cancelled = false;
    let app: Application | null = null;

    (async () => {
      const a = new Application();
      try {
        await a.init({
          background: "#13161b",
          antialias: true,
          resizeTo: el,
          autoDensity: true,
          resolution: window.devicePixelRatio || 1,
        });
      } catch (e) {
        console.error("Pixi init failed (TrackScalar)", e);
        return;
      }
      if (cancelled) {
        try { a.destroy(true); } catch { /* ignore */ }
        return;
      }
      app = a;
      appRef.current = a;
      el.appendChild(a.canvas);

      drawChart(a, track);
      const cursor = new Graphics();
      a.stage.addChild(cursor);
      cursorRef.current = cursor;
    })();

    return () => {
      cancelled = true;
      cursorRef.current = null;
      appRef.current = null;
      if (app) {
        try { app.destroy(true, { children: true }); } catch { /* ignore */ }
      }
    };
    // track.samples is recreated as part of the loader's `done` event,
    // so this effect re-runs once when the session finishes loading.
  }, [track.samples, track.topic]);

  // Playhead cursor: redraw only the cursor line (one Graphics clear +
  // two moveTo/lineTo) on each tick. Chart line itself is never touched.
  useEffect(() => {
    const app = appRef.current;
    const cursor = cursorRef.current;
    if (!app || !cursor) return;
    const samples = track.samples;
    if (samples.length < 2) return;
    const t0 = samples[0].timeNs;
    const tEnd = samples[samples.length - 1].timeNs;
    const span = tEnd - t0;
    if (span <= 0n) return;
    const w = app.renderer.width / app.renderer.resolution;
    const usableW = Math.max(1, w - 2 * PADDING);
    const playNs = currentTimeNs;
    if (playNs < t0 || playNs > tEnd) {
      cursor.clear();
      return;
    }
    const frac = Number(playNs - t0) / Number(span);
    const x = PADDING + frac * usableW;
    cursor.clear();
    cursor.moveTo(x, 4);
    cursor.lineTo(x, HEIGHT - 4);
    cursor.stroke({ color: 0xffffff, width: 1, alpha: 0.6 });
  }, [currentTimeNs, track.samples]);

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
        ref={containerRef}
        style={{ width: "100%", height: HEIGHT }}
        className="border border-border rounded"
      />
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

/** Draw the static line once after samples are known. The playhead cursor
 *  is drawn into a separate Graphics object (see useEffect above) so the
 *  expensive chart polyline is never rebuilt on every tick. */
function drawChart(app: Application, track: ScalarTrack) {
  const { samples } = track;
  if (samples.length < 2) return;
  const w = app.renderer.width / app.renderer.resolution;
  const h = HEIGHT;
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

  const stage = app.stage;
  const chart = new Container();
  stage.addChild(chart);

  const line = new Graphics();
  let started = false;
  for (const s of samples) {
    const frac = Number(s.timeNs - t0) / Number(span);
    const x = PADDING + frac * usableW;
    const y = yBot - ((s.value - minV) / (maxV - minV)) * (yBot - yTop);
    if (!started) {
      line.moveTo(x, y);
      started = true;
    } else {
      line.lineTo(x, y);
    }
  }
  line.stroke({ color: 0xff5a5f, width: 1 });
  chart.addChild(line);

  const labelMax = new Text({
    text: maxV.toFixed(2),
    style: { fill: 0x7d8590, fontSize: 9 },
  });
  labelMax.x = PADDING + 2;
  labelMax.y = yTop - 2;
  chart.addChild(labelMax);

  const labelMin = new Text({
    text: minV.toFixed(2),
    style: { fill: 0x7d8590, fontSize: 9 },
  });
  labelMin.x = PADDING + 2;
  labelMin.y = yBot - 10;
  chart.addChild(labelMin);
}
