import { useEffect, useRef } from "react";
import { Application, Container, Graphics, Text } from "pixi.js";
import { usePlayback } from "../../stores/playback";
import type { DecodedTwist } from "../../workers/types";

type Props = {
  durationNs: bigint;
  twist: DecodedTwist[]; // overlay chart on the timeline
};

const HEIGHT = 140;
const PADDING = 12;

export function Timeline({ durationNs, twist }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const playheadRef = useRef<Graphics | null>(null);
  const dragRef = useRef(false);

  // Build pixi scene once.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const app = new Application();
    appRef.current = app;
    let mounted = true;

    (async () => {
      await app.init({
        background: "#13161b",
        antialias: true,
        resizeTo: el,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
      });
      if (!mounted) {
        app.destroy(true);
        return;
      }
      el.appendChild(app.canvas);

      const stage = app.stage;

      // Time axis container
      const axis = new Container();
      stage.addChild(axis);

      // Chart container
      const chart = new Container();
      stage.addChild(chart);

      // Playhead
      const ph = new Graphics();
      stage.addChild(ph);
      playheadRef.current = ph;

      const draw = () => {
        const w = app.renderer.width / app.renderer.resolution;
        const h = HEIGHT;
        if (Number(durationNs) <= 0) return;
        const usableW = Math.max(1, w - 2 * PADDING);

        // axis
        axis.removeChildren();
        const axisG = new Graphics();
        axisG.moveTo(PADDING, 18).lineTo(w - PADDING, 18).stroke({ color: 0x7d8590, width: 1 });
        axis.addChild(axisG);
        const durationS = Number(durationNs) / 1e9;
        const tickEvery = 1; // seconds
        for (let s = 0; s <= durationS; s += tickEvery) {
          const x = PADDING + (s / durationS) * usableW;
          const tg = new Graphics();
          tg.moveTo(x, 14).lineTo(x, 22).stroke({ color: 0x7d8590, width: 1 });
          axis.addChild(tg);
          if (s % 5 === 0) {
            const t = new Text({
              text: `${s}s`,
              style: { fill: 0x7d8590, fontSize: 10 },
            });
            t.x = x + 2;
            t.y = 2;
            axis.addChild(t);
          }
        }

        // chart: linear.x of /cmd_vel
        chart.removeChildren();
        if (twist.length > 1) {
          let minV = Infinity;
          let maxV = -Infinity;
          for (const t of twist) {
            if (t.linearX < minV) minV = t.linearX;
            if (t.linearX > maxV) maxV = t.linearX;
          }
          if (minV === maxV) {
            minV -= 1;
            maxV += 1;
          }
          const yTop = 30;
          const yBot = h - 8;
          const lineG = new Graphics();
          let started = false;
          for (const t of twist) {
            const tNs = t.timeNs - twist[0].timeNs;
            const frac = Number(tNs) / Number(durationNs);
            const x = PADDING + frac * usableW;
            const y = yBot - ((t.linearX - minV) / (maxV - minV)) * (yBot - yTop);
            if (!started) {
              lineG.moveTo(x, y);
              started = true;
            } else {
              lineG.lineTo(x, y);
            }
          }
          lineG.stroke({ color: 0xff5a5f, width: 1 });
          chart.addChild(lineG);

          // y-axis labels (right edge)
          const ymax = new Text({
            text: `${maxV.toFixed(2)}`,
            style: { fill: 0xff5a5f, fontSize: 10 },
          });
          ymax.x = w - PADDING - 30;
          ymax.y = yTop;
          chart.addChild(ymax);
          const ymin = new Text({
            text: `${minV.toFixed(2)}`,
            style: { fill: 0xff5a5f, fontSize: 10 },
          });
          ymin.x = w - PADDING - 30;
          ymin.y = yBot - 10;
          chart.addChild(ymin);
        }

        drawPlayhead();
      };

      const drawPlayhead = () => {
        const ph = playheadRef.current;
        if (!ph) return;
        ph.clear();
        const w = app.renderer.width / app.renderer.resolution;
        const usableW = Math.max(1, w - 2 * PADDING);
        const { currentTimeNs, durationNs: d } = usePlayback.getState();
        if (d <= 0n) return;
        const frac = Number(currentTimeNs) / Number(d);
        const x = PADDING + frac * usableW;
        ph.moveTo(x, 8).lineTo(x, HEIGHT - 4).stroke({ color: 0xff5a5f, width: 2 });
      };

      const onResize = () => draw();
      const ro = new ResizeObserver(onResize);
      ro.observe(el);
      draw();

      // Subscribe to playback for playhead movement.
      const unsub = usePlayback.subscribe(() => drawPlayhead());

      // Drag-to-scrub
      app.canvas.addEventListener("pointerdown", (e) => {
        dragRef.current = true;
        seekFromX(e.offsetX);
      });
      window.addEventListener("pointermove", (e) => {
        if (!dragRef.current) return;
        const r = app.canvas.getBoundingClientRect();
        seekFromX(e.clientX - r.left);
      });
      window.addEventListener("pointerup", () => (dragRef.current = false));

      function seekFromX(px: number) {
        const w = app.renderer.width / app.renderer.resolution;
        const usableW = Math.max(1, w - 2 * PADDING);
        const frac = Math.max(0, Math.min(1, (px - PADDING) / usableW));
        const { durationNs: d, setTime } = usePlayback.getState();
        setTime(BigInt(Math.floor(frac * Number(d))));
      }

      // Cleanup
      (app as unknown as { _md_cleanup?: () => void })._md_cleanup = () => {
        ro.disconnect();
        unsub();
      };
    })();

    return () => {
      mounted = false;
      const a = appRef.current;
      if (a) {
        const cleanup = (a as unknown as { _md_cleanup?: () => void })._md_cleanup;
        cleanup?.();
        a.destroy(true);
      }
      appRef.current = null;
    };
  }, [durationNs, twist]);

  return (
    <div
      ref={containerRef}
      className="w-full bg-panel border border-border rounded"
      style={{ height: HEIGHT }}
    />
  );
}
