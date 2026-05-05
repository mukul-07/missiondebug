import { useEffect, useRef } from "react";
import { Application, Container, Graphics, Text } from "pixi.js";
import { usePlayback } from "../../stores/playback";
import type { DecodedTwist } from "../../workers/types";

type Props = {
  durationNs: bigint;
  twist: DecodedTwist[];
};

const HEIGHT = 140;
const PADDING = 12;

type DrawState = {
  durationNs: bigint;
  twist: DecodedTwist[];
};

export function Timeline({ durationNs, twist }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  // Latest props mirrored into a ref so the pixi side can read them
  // without re-running the init effect.
  const stateRef = useRef<DrawState>({ durationNs, twist });
  stateRef.current = { durationNs, twist };

  // Imperative trigger to redraw — set by the init effect, called by the data effect.
  const redrawRef = useRef<(() => void) | null>(null);

  // INIT: runs once on mount. Sets up the Pixi app.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let cancelled = false;
    let app: Application | null = null;
    let unsubPlayback: (() => void) | null = null;
    let resizeObs: ResizeObserver | null = null;
    let onPointerMove: ((e: PointerEvent) => void) | null = null;
    let onPointerUp: (() => void) | null = null;

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
        console.error("Pixi init failed", e);
        return;
      }
      if (cancelled) {
        // Component unmounted before init finished — destroy quietly.
        try { a.destroy(true); } catch { /* ignore */ }
        return;
      }
      app = a;
      el.appendChild(a.canvas);

      const stage = a.stage;
      const axis = new Container();
      const chart = new Container();
      const playhead = new Graphics();
      stage.addChild(axis);
      stage.addChild(chart);
      stage.addChild(playhead);

      const drawAxisAndChart = () => {
        if (!app) return;
        const w = app.renderer.width / app.renderer.resolution;
        const h = HEIGHT;
        const { durationNs: d, twist: tw } = stateRef.current;
        if (d <= 0n) {
          axis.removeChildren();
          chart.removeChildren();
          drawPlayhead();
          return;
        }
        const usableW = Math.max(1, w - 2 * PADDING);

        // Axis
        axis.removeChildren();
        const axisG = new Graphics();
        axisG.moveTo(PADDING, 18).lineTo(w - PADDING, 18).stroke({ color: 0x7d8590, width: 1 });
        axis.addChild(axisG);
        const durationS = Number(d) / 1e9;
        for (let s = 0; s <= durationS; s += 1) {
          const x = PADDING + (s / durationS) * usableW;
          const tg = new Graphics();
          tg.moveTo(x, 14).lineTo(x, 22).stroke({ color: 0x7d8590, width: 1 });
          axis.addChild(tg);
          if (s % 5 === 0) {
            const t = new Text({ text: `${s}s`, style: { fill: 0x7d8590, fontSize: 10 } });
            t.x = x + 2;
            t.y = 2;
            axis.addChild(t);
          }
        }

        // Chart
        chart.removeChildren();
        if (tw.length > 1) {
          let minV = Infinity;
          let maxV = -Infinity;
          for (const m of tw) {
            if (m.linearX < minV) minV = m.linearX;
            if (m.linearX > maxV) maxV = m.linearX;
          }
          if (minV === maxV) {
            minV -= 1;
            maxV += 1;
          }
          const yTop = 30;
          const yBot = h - 8;
          const lineG = new Graphics();
          let started = false;
          const t0 = tw[0].timeNs;
          for (const m of tw) {
            const tNs = m.timeNs - t0;
            const frac = Number(tNs) / Number(d);
            const x = PADDING + frac * usableW;
            const y = yBot - ((m.linearX - minV) / (maxV - minV)) * (yBot - yTop);
            if (!started) {
              lineG.moveTo(x, y);
              started = true;
            } else {
              lineG.lineTo(x, y);
            }
          }
          lineG.stroke({ color: 0xff5a5f, width: 1 });
          chart.addChild(lineG);

          const ymax = new Text({
            text: maxV.toFixed(2),
            style: { fill: 0xff5a5f, fontSize: 10 },
          });
          ymax.x = w - PADDING - 30;
          ymax.y = yTop;
          chart.addChild(ymax);
          const ymin = new Text({
            text: minV.toFixed(2),
            style: { fill: 0xff5a5f, fontSize: 10 },
          });
          ymin.x = w - PADDING - 30;
          ymin.y = yBot - 10;
          chart.addChild(ymin);
        }

        drawPlayhead();
      };

      const drawPlayhead = () => {
        if (!app) return;
        playhead.clear();
        const w = app.renderer.width / app.renderer.resolution;
        const usableW = Math.max(1, w - 2 * PADDING);
        const { currentTimeNs, durationNs: d } = usePlayback.getState();
        if (d <= 0n) return;
        const frac = Number(currentTimeNs) / Number(d);
        const x = PADDING + frac * usableW;
        playhead.moveTo(x, 8).lineTo(x, HEIGHT - 4).stroke({ color: 0xff5a5f, width: 2 });
      };

      redrawRef.current = drawAxisAndChart;

      // Resize observer.
      resizeObs = new ResizeObserver(() => drawAxisAndChart());
      resizeObs.observe(el);

      // Playhead follows playback state.
      unsubPlayback = usePlayback.subscribe(() => drawPlayhead());

      // Drag-to-scrub.
      let dragging = false;
      const seekFromX = (px: number) => {
        if (!app) return;
        const w = app.renderer.width / app.renderer.resolution;
        const usableW = Math.max(1, w - 2 * PADDING);
        const frac = Math.max(0, Math.min(1, (px - PADDING) / usableW));
        const { durationNs: d, setTime } = usePlayback.getState();
        setTime(BigInt(Math.floor(frac * Number(d))));
      };
      a.canvas.addEventListener("pointerdown", (e) => {
        dragging = true;
        seekFromX(e.offsetX);
      });
      onPointerMove = (e: PointerEvent) => {
        if (!dragging || !app) return;
        const r = app.canvas.getBoundingClientRect();
        seekFromX(e.clientX - r.left);
      };
      onPointerUp = () => { dragging = false; };
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);

      drawAxisAndChart();
    })();

    return () => {
      cancelled = true;
      redrawRef.current = null;
      try { resizeObs?.disconnect(); } catch { /* ignore */ }
      try { unsubPlayback?.(); } catch { /* ignore */ }
      if (onPointerMove) window.removeEventListener("pointermove", onPointerMove);
      if (onPointerUp) window.removeEventListener("pointerup", onPointerUp);
      if (app) {
        try { app.destroy(true); } catch (e) { console.warn("pixi destroy", e); }
      }
    };
  }, []);

  // DATA: redraw whenever twist or duration changes.
  useEffect(() => {
    redrawRef.current?.();
  }, [durationNs, twist]);

  return (
    <div
      ref={containerRef}
      className="w-full bg-panel border border-border rounded"
      style={{ height: HEIGHT }}
    />
  );
}
