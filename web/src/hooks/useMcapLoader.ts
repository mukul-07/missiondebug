import { useEffect, useRef, useState } from "react";
import type {
  ChannelInfo,
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

export interface LoadedSession {
  channels: ChannelInfo[];
  startNs: bigint;
  endNs: bigint;
  videoByTopic: Map<string, DecodedVideoFrame[]>;
  twistByTopic: Map<string, DecodedTwist[]>;
  tfByTopic: Map<string, DecodedTf[]>;
  scalarByTopic: Map<string, ScalarTrack>;
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
        case "done": {
          for (const arr of cur.videoByTopic.values())
            arr.sort((a, b) => Number(a.timeNs - b.timeNs));
          for (const arr of cur.twistByTopic.values())
            arr.sort((a, b) => Number(a.timeNs - b.timeNs));
          for (const arr of cur.tfByTopic.values())
            arr.sort((a, b) => Number(a.timeNs - b.timeNs));
          for (const track of cur.scalarByTopic.values())
            track.samples.sort((a, b) => Number(a.timeNs - b.timeNs));
          // Create new Map references so React's reference-equality picks
          // up the data accumulated via in-place mutation in the cases above.
          const updated = {
            ...cur,
            videoByTopic: new Map(cur.videoByTopic),
            twistByTopic: new Map(cur.twistByTopic),
            tfByTopic: new Map(cur.tfByTopic),
            scalarByTopic: new Map(cur.scalarByTopic),
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
