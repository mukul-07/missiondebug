import { useEffect, useRef, useState } from "react";
import type {
  ChannelInfo,
  DecodedOther,
  DecodedScalar,
  DecodedTf,
  DecodedTwist,
  DecodedVideoFrame,
  WorkerOutbound,
} from "../workers/types";

export interface ScalarTrack {
  topic: string;
  fieldPath: string;
  samples: DecodedScalar[];
}

/**
 * v2 P1.7.5 — windowed buffer of decoded messages for the JSON inspector.
 * Caps at OTHER_MSGS_CAP_PER_TOPIC entries per topic. When the cap is
 * reached, the oldest message drops (FIFO).
 */
export interface OtherTrack {
  topic: string;
  messages: DecodedOther[];
}

const OTHER_MSGS_CAP_PER_TOPIC = 2000;

export interface LoadedSession {
  channels: ChannelInfo[];
  startNs: bigint;
  endNs: bigint;
  videoByTopic: Map<string, DecodedVideoFrame[]>;
  twistByTopic: Map<string, DecodedTwist[]>;
  tfByTopic: Map<string, DecodedTf[]>;
  scalarByTopic: Map<string, ScalarTrack>;
  otherByTopic: Map<string, OtherTrack>;
  done: boolean;
  error: string | null;
}

const empty = (): LoadedSession => ({
  channels: [],
  startNs: 0n,
  endNs: 0n,
  videoByTopic: new Map(),
  twistByTopic: new Map(),
  tfByTopic: new Map(),
  scalarByTopic: new Map(),
  otherByTopic: new Map(),
  done: false,
  error: null,
});

export function useMcapLoader(url: string | null): LoadedSession {
  const [state, setState] = useState<LoadedSession>(empty);
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    if (!url) return;
    const next = empty();
    setState(next);
    stateRef.current = next;

    const worker = new Worker(
      new URL("../workers/mcap-decoder.ts", import.meta.url),
      { type: "module" },
    );

    // Surface module-level errors that would otherwise hang "loading..." forever.
    const onErr = (e: ErrorEvent) => {
      const msg = `${e.message || "worker error"} (${e.filename}:${e.lineno})`;
      console.error("MCAP worker error:", e);
      const cur = stateRef.current;
      const updated = { ...cur, error: msg, done: true };
      stateRef.current = updated;
      setState(updated);
    };
    worker.addEventListener("error", onErr);
    worker.addEventListener("messageerror", (e) => {
      console.error("MCAP worker messageerror:", e);
    });

    worker.addEventListener("message", (ev: MessageEvent<WorkerOutbound>) => {
      const m = ev.data;
      const cur = stateRef.current;
      switch (m.type) {
        case "channels": {
          const startNs = BigInt(m.startNs);
          const endNs = BigInt(m.endNs);
          const updated: LoadedSession = {
            ...cur,
            channels: m.channels,
            startNs,
            endNs,
          };
          stateRef.current = updated;
          setState(updated);
          break;
        }
        case "video": {
          const arr = cur.videoByTopic.get(m.frame.topic) ?? [];
          arr.push(m.frame);
          cur.videoByTopic.set(m.frame.topic, arr);
          break;
        }
        case "twist": {
          const arr = cur.twistByTopic.get(m.msg.topic) ?? [];
          arr.push(m.msg);
          cur.twistByTopic.set(m.msg.topic, arr);
          break;
        }
        case "tf": {
          const arr = cur.tfByTopic.get(m.msg.topic) ?? [];
          arr.push(m.msg);
          cur.tfByTopic.set(m.msg.topic, arr);
          break;
        }
        case "scalar": {
          let track = cur.scalarByTopic.get(m.msg.topic);
          if (track === undefined) {
            track = {
              topic: m.msg.topic,
              fieldPath: m.msg.fieldPath,
              samples: [],
            };
            cur.scalarByTopic.set(m.msg.topic, track);
          }
          track.samples.push(m.msg);
          break;
        }
        case "other": {
          let track = cur.otherByTopic.get(m.msg.topic);
          if (track === undefined) {
            track = { topic: m.msg.topic, messages: [] };
            cur.otherByTopic.set(m.msg.topic, track);
          }
          track.messages.push(m.msg);
          // FIFO cap — drop oldest when over.
          if (track.messages.length > OTHER_MSGS_CAP_PER_TOPIC) {
            track.messages.shift();
          }
          break;
        }
        case "done": {
          for (const arr of cur.videoByTopic.values())
            arr.sort((a, b) => Number(a.timeNs - b.timeNs));
          for (const arr of cur.twistByTopic.values())
            arr.sort((a, b) => Number(a.timeNs - b.timeNs));
          for (const arr of cur.tfByTopic.values())
            arr.sort((a, b) => Number(a.timeNs - b.timeNs));
          for (const track of cur.scalarByTopic.values())
            track.samples.sort((a, b) => Number(a.timeNs - b.timeNs));
          for (const track of cur.otherByTopic.values())
            track.messages.sort((a, b) => Number(a.timeNs - b.timeNs));
          // Create new Map references so React's reference-equality picks
          // up the data accumulated via in-place mutation in the cases above.
          const updated = {
            ...cur,
            videoByTopic: new Map(cur.videoByTopic),
            twistByTopic: new Map(cur.twistByTopic),
            tfByTopic: new Map(cur.tfByTopic),
            scalarByTopic: new Map(cur.scalarByTopic),
            otherByTopic: new Map(cur.otherByTopic),
            done: true,
          };
          stateRef.current = updated;
          setState(updated);
          console.info(
            "loaded",
            Object.values(m.counts).reduce((a, b) => a + b, 0),
            "messages across",
            Object.keys(m.counts).length,
            "topics",
          );
          break;
        }
        case "error": {
          const updated = { ...cur, error: m.message };
          stateRef.current = updated;
          setState(updated);
          break;
        }
      }
    });

    worker.postMessage({ type: "load", url });
    return () => worker.terminate();
  }, [url]);

  return state;
}
