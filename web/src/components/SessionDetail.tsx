import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listAnnotations } from "../api/annotations";
import { getSession, mcapUrl } from "../api/sessions";
import { useMcapLoader } from "../hooks/useMcapLoader";
import { usePlayback } from "../stores/playback";
import { AnnotationsPanel } from "./AnnotationsPanel";
import { Timeline } from "./timeline/Timeline";
import { TrackVideo } from "./timeline/TrackVideo";
import { TrackPose } from "./timeline/TrackPose";
import { Button } from "./ui/Button";

export function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: meta } = useQuery({
    queryKey: ["session", id],
    queryFn: () => getSession(id!),
    enabled: !!id,
  });

  const url = id ? mcapUrl(id) : null;
  const loaded = useMcapLoader(url);
  const setDuration = usePlayback((s) => s.setDuration);
  const isPlaying = usePlayback((s) => s.isPlaying);
  const speed = usePlayback((s) => s.speed);
  const toggle = usePlayback((s) => s.toggle);
  const step = usePlayback((s) => s.step);
  const setTime = usePlayback((s) => s.setTime);
  const currentTimeNs = usePlayback((s) => s.currentTimeNs);
  const durationNs = usePlayback((s) => s.durationNs);

  // Initialize playback duration when channels first arrive.
  useEffect(() => {
    if (loaded.endNs > loaded.startNs) {
      setDuration(loaded.startNs, loaded.endNs - loaded.startNs);
    }
  }, [loaded.startNs, loaded.endNs, setDuration]);

  // ---------- shareable ?t= deep link ----------
  const [searchParams] = useSearchParams();
  const initialTimeApplied = useRef(false);
  // Apply ?t=<seconds> ONCE, after duration is known.
  useEffect(() => {
    if (initialTimeApplied.current) return;
    if (durationNs <= 0n) return;
    const tParam = searchParams.get("t");
    if (tParam === null) {
      initialTimeApplied.current = true;
      return;
    }
    const seconds = Number(tParam);
    if (Number.isFinite(seconds) && seconds >= 0) {
      const ns = BigInt(Math.round(seconds * 1e9));
      setTime(ns);
    }
    initialTimeApplied.current = true;
  }, [durationNs, searchParams, setTime]);

  // Debounced URL sync as the playhead moves.
  useEffect(() => {
    if (!initialTimeApplied.current || durationNs <= 0n) return;
    const handle = window.setTimeout(() => {
      const seconds = Number(currentTimeNs) / 1e9;
      const url = new URL(window.location.href);
      url.searchParams.set("t", seconds.toFixed(2));
      window.history.replaceState({}, "", url.toString());
    }, 250);
    return () => window.clearTimeout(handle);
  }, [currentTimeNs, durationNs]);

  // Copy link state.
  const [copied, setCopied] = useState(false);
  const onCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      console.error("clipboard write failed", e);
    }
  };

  // Playback rAF loop.
  const lastTsRef = useRef<number | null>(null);
  useEffect(() => {
    if (!isPlaying) {
      lastTsRef.current = null;
      return;
    }
    let raf = 0;
    const tick = (ts: number) => {
      const last = lastTsRef.current;
      lastTsRef.current = ts;
      if (last !== null) {
        const dtMs = ts - last;
        const advance = BigInt(Math.round(dtMs * 1e6 * speed));
        const { currentTimeNs: cur, durationNs: dur } = usePlayback.getState();
        const next = cur + advance;
        if (next >= dur) {
          setTime(dur);
          usePlayback.getState().pause();
          return;
        }
        setTime(next);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [isPlaying, speed, setTime]);

  // Keyboard shortcuts.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || t?.isContentEditable) return;
      if (e.code === "Space") {
        e.preventDefault();
        toggle();
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        step(BigInt(e.shiftKey ? -1_000_000_000 : -100_000_000));
      } else if (e.code === "ArrowRight") {
        e.preventDefault();
        step(BigInt(e.shiftKey ? 1_000_000_000 : 100_000_000));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, step]);

  const videoTopics = useMemo(
    () => Array.from(loaded.videoByTopic.keys()).slice(0, 2),
    [loaded.videoByTopic],
  );
  const tfTopics = useMemo(() => Array.from(loaded.tfByTopic.keys()), [loaded.tfByTopic]);
  const twistTopics = useMemo(
    () => Array.from(loaded.twistByTopic.keys()),
    [loaded.twistByTopic],
  );

  const primaryTwist = twistTopics[0] ? loaded.twistByTopic.get(twistTopics[0]) ?? [] : [];

  const { data: annotations = [] } = useQuery({
    queryKey: ["annotations", id],
    queryFn: () => listAnnotations(id!),
    enabled: !!id,
  });
  const timelineAnnotations = useMemo(
    () => annotations.map((a) => ({ id: a.id, timeNs: a.time_ns, body: a.body })),
    [annotations],
  );

  return (
    <div className="p-4 grid gap-3">
      <div className="flex items-baseline gap-3">
        <h2 className="text-lg font-mono">{meta?.id ?? id}</h2>
        {meta?.label ? (
          <span className="px-2 py-0.5 rounded bg-accent/20 text-accent text-xs">
            {meta.label}
          </span>
        ) : null}
        <span className="text-xs text-muted">
          {(Number(durationNs) / 1e9).toFixed(1)}s · {loaded.channels.length} channels ·{" "}
          {loaded.error ? <span className="text-accent">err: {loaded.error}</span> : loaded.done ? "loaded" : "loading…"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {videoTopics.length > 0 ? (
          videoTopics.map((t) => (
            <TrackVideo key={t} label={t} frames={loaded.videoByTopic.get(t) ?? []} />
          ))
        ) : (
          <div className="col-span-2 text-muted text-sm">No video topics in this session.</div>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3">
        {tfTopics.length > 0 ? (
          tfTopics.slice(0, 1).map((t) => (
            <TrackPose key={t} msgs={loaded.tfByTopic.get(t) ?? []} />
          ))
        ) : (
          <div className="text-muted text-sm">No /tf data.</div>
        )}
        <div className="col-span-2 text-xs text-muted self-center">
          Space: play/pause · ←/→: step 100ms · Shift+←/→: step 1s
        </div>
      </div>

      <Timeline durationNs={durationNs} twist={primaryTwist} annotations={timelineAnnotations} />

      <div className="flex items-center gap-2">
        <Button onClick={toggle}>{isPlaying ? "Pause" : "Play"}</Button>
        <span className="text-xs text-muted font-mono">
          {(Number(currentTimeNs) / 1e9).toFixed(2)} / {(Number(durationNs) / 1e9).toFixed(2)} s
        </span>
        <Button variant="ghost" onClick={onCopyLink} className="text-xs ml-auto">
          {copied ? "✓ Link copied" : "Copy link"}
        </Button>
      </div>

      {id ? <AnnotationsPanel sessionId={id} /> : null}
    </div>
  );
}
