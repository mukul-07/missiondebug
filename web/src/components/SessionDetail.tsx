import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listAnnotations } from "../api/annotations";
import { getSession, mcapUrl } from "../api/sessions";
import { copyText } from "../lib/clipboard";
import { useMcapLoader } from "../hooks/useMcapLoader";
import { usePlayback } from "../stores/playback";
import { AnnotationsPanel } from "./AnnotationsPanel";
import { Timeline } from "./timeline/Timeline";
import { TrackVideo } from "./timeline/TrackVideo";
import { TrackPose } from "./timeline/TrackPose";
import { TrackJoints } from "./timeline/TrackJoints";
import { TrackScalar } from "./timeline/TrackScalar";
import { TrackJsonInspector } from "./timeline/TrackJsonInspector";
import { TopicListExpander } from "./TopicListExpander";
import { FoxgloveButton } from "./FoxgloveButton";
import { Button } from "./ui/Button";
import { Card } from "./ui/Card";
import { SimilarIncidents } from "./SimilarIncidents";
import { ResolutionPanel } from "./ResolutionPanel";

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
  const [searchParams, setSearchParams] = useSearchParams();
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

  // Copy link — three-tier fallback lives in lib/clipboard (plain-HTTP
  // fleet hubs can't use navigator.clipboard; see copyText).
  const [copied, setCopied] = useState(false);
  const onCopyLink = async () => {
    if (await copyText(window.location.href, "Copy this link:")) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
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
  const jointsTopics = useMemo(
    () => Array.from(loaded.jointsByTopic.keys()),
    [loaded.jointsByTopic],
  );
  const twistTopics = useMemo(
    () => Array.from(loaded.twistByTopic.keys()),
    [loaded.twistByTopic],
  );

  // P1.7.6 — configurable velocity-chart source. Real fleets often
  // have multiple Twist topics (nav/cmd_vel, dock/cmd_vel,
  // <robot>/base_controller/cmd_vel). Pick from the URL param if set,
  // else prefer "/cmd_vel" exactly, else fall back to the first
  // available Twist topic. Lets engineers deep-link to a specific
  // control-source view: /sessions/X?vel=/dock/cmd_vel.
  const velParam = searchParams.get("vel");
  const primaryTwistTopic = useMemo(() => {
    if (velParam && twistTopics.includes(velParam)) return velParam;
    if (twistTopics.includes("/cmd_vel")) return "/cmd_vel";
    return twistTopics[0] ?? null;
  }, [velParam, twistTopics]);
  const primaryTwist = primaryTwistTopic
    ? loaded.twistByTopic.get(primaryTwistTopic) ?? []
    : [];

  const { data: annotations = [] } = useQuery({
    queryKey: ["annotations", id],
    queryFn: () => listAnnotations(id!),
    enabled: !!id,
  });
  const timelineAnnotations = useMemo(
    () => annotations.map((a) => ({ id: a.id, timeNs: a.time_ns, body: a.body })),
    [annotations],
  );

  // At fleet scale the incident metadata outlives the raw MCAP: retention
  // tiers recordings to cold storage or deletes them, agents go offline,
  // S3 objects expire. "Metadata exists, bytes don't" is a NORMAL state,
  // not an error — so when the recording can't be fetched and nothing
  // decoded, we render a calm "recording unavailable" panel instead of a
  // red error + a wall of empty players. The incident-memory surfaces
  // (summary, similarity, resolution) stay fully visible — that's the
  // durable value, and the whole pitch is that it survives the recording.
  const recordingUnavailable = !!loaded.error && loaded.channels.length === 0;

  return (
    <div className="p-4 grid gap-3">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h2 className="text-lg font-mono">{meta?.id ?? id}</h2>
        {meta?.robot_id ? (
          <a
            href={`/?robot=${encodeURIComponent(meta.robot_id)}`}
            title={`See other sessions from ${meta.robot_id}`}
            className="text-xs px-1.5 py-0.5 rounded bg-bg border border-border font-mono text-muted hover:text-text hover:border-accent"
          >
            {meta.robot_id}
          </a>
        ) : null}
        {meta?.subsystem ? (
          <span
            title={`subsystem: ${meta.subsystem}`}
            className="text-xs px-1.5 py-0.5 rounded bg-bg border border-border font-mono text-muted"
          >
            {meta.subsystem}
          </span>
        ) : null}
        {meta?.label ? (
          <span className="px-2 py-0.5 rounded bg-accent/20 text-accent text-xs">
            {meta.label}
          </span>
        ) : null}
        <span className="text-xs text-muted">
          {(Number(durationNs) / 1e9).toFixed(1)}s · {loaded.channels.length} channels ·{" "}
          {recordingUnavailable ? (
            "recording unavailable"
          ) : loaded.error ? (
            <span className="text-accent">err: {loaded.error}</span>
          ) : loaded.done ? (
            "loaded"
          ) : (
            "loading…"
          )}
        </span>
      </div>

      <div className="grid md:grid-cols-2 gap-3">
        {meta?.summary ? (
          <Card className="bg-bg/50">
            <div className="text-[10px] uppercase tracking-wide text-muted mb-1">
              Summary
            </div>
            <div className="text-sm text-text leading-relaxed">{meta.summary}</div>
          </Card>
        ) : (
          <div />
        )}
        {id ? (
          <SimilarIncidents sessionId={id} hasSummary={!!meta?.summary} />
        ) : null}
      </div>

      {id ? <ResolutionPanel sessionId={id} /> : null}

      {recordingUnavailable ? (
        <Card className="bg-bg/50">
          <div className="text-[10px] uppercase tracking-wide text-muted mb-1">
            Recording
          </div>
          <div className="text-sm text-text/80 leading-relaxed">
            {meta?.cold_at ? (
              <>
                The recording for this incident was tiered out by a lifecycle
                policy on{" "}
                <span className="text-text">
                  {new Date(meta.cold_at).toLocaleDateString()}
                </span>{" "}
                — the bytes are released, the incident record is kept.
              </>
            ) : (
              <>
                The recording for this incident is no longer available — it may
                have been tiered to cold storage, removed by a retention policy,
                or its reporting agent is offline.
              </>
            )}
          </div>
          <div className="text-xs text-muted mt-2">
            The incident record above — summary, similar incidents, and
            resolution — is preserved independently of the raw recording.
          </div>
        </Card>
      ) : (
      <>
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

      {/* Manipulator per-joint charts — one line per joint from any
          sensor_msgs/JointState topic (position/velocity/effort selectable).
          Renders arm motion the generic scalar grid skips (joint arrays). */}
      {jointsTopics.length > 0
        ? jointsTopics.map((t) => (
            <TrackJoints key={t} topic={t} samples={loaded.jointsByTopic.get(t) ?? []} />
          ))
        : null}

      {/* P1.7.4 — auto-rendered scalar charts for every "other" topic
          that yielded a numeric leaf. Sorted by sample count desc so
          the most-active topics show first. Filterable via ?q= so the
          engineer can narrow the grid to the topics relevant to the
          incident they're investigating. */}
      {loaded.scalarByTopic.size > 0 ? (
        <ScalarGrid
          tracks={Array.from(loaded.scalarByTopic.values()).sort(
            (a, b) => b.samples.length - a.samples.length,
          )}
          query={searchParams.get("q") ?? ""}
          onQueryChange={(q) => {
            const next = new URLSearchParams(searchParams);
            if (q) next.set("q", q);
            else next.delete("q");
            setSearchParams(next, { replace: true });
          }}
        />
      ) : null}

      {/* P1.7.5 — JSON inspector for every "other" topic. One panel,
          dropdown selector across topics, tree view synced to the
          playhead. Useful for state machines, custom messages, and
          structured topics where the scalar chart's first-leaf
          heuristic isn't enough. */}
      {loaded.otherByTopic.size > 0 ? (
        <TrackJsonInspector
          tracks={Array.from(loaded.otherByTopic.values())}
        />
      ) : null}

      <div className="flex items-center gap-2 flex-wrap">
        <Button onClick={toggle}>{isPlaying ? "Pause" : "Play"}</Button>
        <span className="text-xs text-muted font-mono">
          {(Number(currentTimeNs) / 1e9).toFixed(2)} / {(Number(durationNs) / 1e9).toFixed(2)} s
        </span>
        {primaryTwistTopic && twistTopics.length > 1 ? (
          <label className="text-xs text-muted flex items-center gap-1 ml-2">
            velocity:
            <select
              value={primaryTwistTopic}
              onChange={(e) => {
                searchParams.set("vel", e.target.value);
                window.history.replaceState(
                  {},
                  "",
                  `${window.location.pathname}?${searchParams.toString()}`,
                );
                // Force re-render via state — searchParams alone via
                // useSearchParams would also work but we don't have a
                // setSearchParams here yet; reload-light approach.
                window.dispatchEvent(new PopStateEvent("popstate"));
              }}
              className="bg-bg border border-border rounded px-1 py-0.5 text-xs font-mono outline-none focus:border-accent"
            >
              {twistTopics.map((t) => (
                <option key={t} value={t} className="bg-panel">
                  {t}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <Button variant="ghost" onClick={onCopyLink} className="text-xs ml-auto">
          {copied ? "✓ Link copied" : "Copy link"}
        </Button>
        <FoxgloveButton sessionId={id ?? ""} />
      </div>
      </>
      )}

      {meta?.topics && meta.topics.length > 0 ? (
        <TopicListExpander topics={meta.topics} />
      ) : null}

      {id ? <AnnotationsPanel sessionId={id} /> : null}
    </div>
  );
}

type ScalarGridProps = {
  tracks: import("../hooks/useMcapLoader").ScalarTrack[];
  query: string;
  onQueryChange: (q: string) => void;
};

function ScalarGrid({ tracks, query, onQueryChange }: ScalarGridProps) {
  // Chips are committed substrings (?q=imu,motor → ["imu","motor"]).
  // In-progress draft is what the user is typing right now; it filters
  // live on top of the chips and becomes a chip on Enter.
  const chips = query
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0);
  const [draft, setDraft] = useState("");
  const draftLower = draft.trim().toLowerCase();

  // OR-match across chips. The live draft, if any, also acts as an OR
  // chip so the user sees what their next commit will add.
  const activeNeedles = draftLower ? [...chips, draftLower] : chips;
  const filtered = activeNeedles.length === 0
    ? tracks
    : tracks.filter((t) => {
        const name = t.topic.toLowerCase();
        return activeNeedles.some((n) => name.includes(n));
      });

  const commitDraft = () => {
    if (!draftLower) return;
    if (chips.includes(draftLower)) {
      setDraft("");
      return;
    }
    onQueryChange([...chips, draftLower].join(","));
    setDraft("");
  };
  const removeChip = (c: string) => {
    onQueryChange(chips.filter((x) => x !== c).join(","));
  };
  const clearAll = () => {
    onQueryChange("");
    setDraft("");
  };

  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          type="text"
          placeholder={chips.length === 0 ? "Filter topics… (Enter to add)" : "+ add another…"}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commitDraft();
            } else if (e.key === "Backspace" && draft === "" && chips.length > 0) {
              // Backspace on empty input removes the last chip.
              removeChip(chips[chips.length - 1]);
            }
          }}
          className="bg-bg border border-border rounded px-2 py-1 text-xs font-mono outline-none focus:border-accent w-56"
        />
        {chips.map((c) => (
          <span
            key={c}
            className="inline-flex items-center gap-1 bg-panel border border-border rounded px-2 py-0.5 text-xs font-mono"
          >
            {c}
            <button
              type="button"
              onClick={() => removeChip(c)}
              aria-label={`Remove ${c} filter`}
              className="text-muted hover:text-fg"
            >
              ×
            </button>
          </span>
        ))}
        {chips.length > 0 ? (
          <button
            type="button"
            onClick={clearAll}
            className="text-xs text-muted hover:text-fg underline"
          >
            clear
          </button>
        ) : null}
        <span className="text-xs text-muted ml-auto">
          {filtered.length} of {tracks.length} scalar topic
          {tracks.length === 1 ? "" : "s"}
        </span>
      </div>
      {filtered.length > 0 ? (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {filtered.map((track) => (
            <TrackScalar key={track.topic} track={track} />
          ))}
        </div>
      ) : (
        <div className="text-muted text-xs">No topics match the current filter.</div>
      )}
    </div>
  );
}
