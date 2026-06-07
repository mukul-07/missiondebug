/// <reference lib="webworker" />
// MCAP-decoder Web Worker.
//
// Loads the full MCAP, then iterates messages via McapIndexedReader (which
// transparently handles chunked / zstd-compressed files) and decodes ROS 2
// messages with @foxglove/rosmsg2-serialization.

import { MessageReader } from "@foxglove/rosmsg2-serialization";
import { parse as parseRos2Schema } from "@foxglove/rosmsg";
import { McapIndexedReader } from "@mcap/core";
import { decompress as zstdDecompress } from "fzstd";
import type {
  ChannelInfo,
  ChannelKind,
  WorkerInbound,
  WorkerOutbound,
} from "./types";

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope;

const decompressHandlers = {
  zstd: (data: Uint8Array, decompressedSize: bigint) => {
    const out = new Uint8Array(Number(decompressedSize));
    zstdDecompress(data, out);
    return out;
  },
};

function classify(schemaName: string): ChannelKind {
  if (schemaName.endsWith("CompressedImage")) return "video";
  if (schemaName.endsWith("Twist")) return "twist";
  if (schemaName.endsWith("TFMessage")) return "tf";
  if (schemaName.endsWith("JointState")) return "joints";
  return "other";
}

/**
 * P1.7.4 — heuristic field picker for the auto-scalar chart.
 *
 * Given a decoded message, find the first useful numeric leaf and
 * return its dotted path + value. Skips fields that look like metadata
 * (sequence numbers, timestamps, frame ids) and primitive arrays
 * (positions, velocities — those need an index, deferred to manual
 * config).
 *
 * Returns null if no useful scalar found.
 */
const SKIP_KEYS = new Set([
  "seq",
  "sec",
  "nanosec",
  "stamp",
  "frame_id",
  "child_frame_id",
  "header",
  "covariance",
]);

function pickScalarField(
  value: unknown,
  path: string[] = [],
  depth = 0,
): { fieldPath: string; value: number } | null {
  // Guard against runaway nested messages.
  if (depth > 6) return null;
  if (value === null || value === undefined) return null;

  // Leaf number.
  if (typeof value === "number" && Number.isFinite(value)) {
    return { fieldPath: path.join("."), value };
  }
  // BigInt timestamps are common in ROS — convert if small enough.
  if (typeof value === "bigint") {
    const n = Number(value);
    if (Number.isFinite(n)) return { fieldPath: path.join("."), value: n };
    return null;
  }
  // Don't drill into arrays — would need index disambiguation. The user
  // can pick array elements via manual config in a future iteration.
  if (Array.isArray(value)) return null;

  if (typeof value === "object") {
    for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
      if (SKIP_KEYS.has(key)) continue;
      const got = pickScalarField(v, [...path, key], depth + 1);
      if (got !== null) return got;
    }
  }
  return null;
}

class BufferReadable {
  constructor(private readonly buf: Uint8Array) {}
  async size(): Promise<bigint> {
    return BigInt(this.buf.byteLength);
  }
  async read(offset: bigint, length: bigint): Promise<Uint8Array> {
    const start = Number(offset);
    const end = start + Number(length);
    return this.buf.subarray(start, end);
  }
}

async function loadAndDecode(url: string): Promise<void> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`fetch failed: ${resp.status}`);
  const ab = await resp.arrayBuffer();
  const buf = new Uint8Array(ab);

  const reader = await McapIndexedReader.Initialize({
    readable: new BufferReadable(buf),
    decompressHandlers,
  });

  // Build per-channel decoder.
  type ChEntry = { info: ChannelInfo; reader: MessageReader | null };
  const channelEntries = new Map<number, ChEntry>();
  for (const channel of reader.channelsById.values()) {
    const schema = channel.schemaId === 0 ? undefined : reader.schemasById.get(channel.schemaId);
    const schemaName = schema?.name ?? "";
    let msgReader: MessageReader | null = null;
    if (schema && schema.encoding === "ros2msg") {
      try {
        const text = new TextDecoder().decode(schema.data);
        const defs = parseRos2Schema(text, { ros2: true });
        msgReader = new MessageReader(defs);
      } catch (e) {
        console.warn("schema parse failed", schemaName, e);
      }
    }
    channelEntries.set(channel.id, {
      info: {
        id: channel.id,
        topic: channel.topic,
        schemaName,
        kind: classify(schemaName),
      },
      reader: msgReader,
    });
  }

  // Announce channels and start/end times up front.
  const stats = reader.statistics;
  const startNs = (stats?.messageStartTime ?? 0n).toString();
  const endNs = (stats?.messageEndTime ?? 0n).toString();
  ctx.postMessage({
    type: "channels",
    channels: Array.from(channelEntries.values()).map((c) => c.info),
    startNs,
    endNs,
  } satisfies WorkerOutbound);

  const counts: Record<string, number> = {};
  for (const c of channelEntries.values()) counts[c.info.topic] = 0;

  // P1.7.4 — first-message-wins field path per "other" topic. Set on
  // the first message that yields a numeric leaf via pickScalarField,
  // then walked the same way on every subsequent message of that topic.
  const scalarFieldByTopic = new Map<string, string | "none">();

  for await (const msg of reader.readMessages()) {
    const ch = channelEntries.get(msg.channelId);
    if (!ch) continue;
    counts[ch.info.topic] = (counts[ch.info.topic] ?? 0) + 1;

    if (!ch.reader) continue;
    let decoded: unknown;
    try {
      decoded = ch.reader.readMessage(msg.data);
    } catch (e) {
      console.warn("decode failed", ch.info.topic, e);
      continue;
    }

    const t = msg.logTime;
    if (ch.info.kind === "video") {
      const m = decoded as { format: string; data: Uint8Array };
      const copy = new Uint8Array(m.data);
      ctx.postMessage(
        {
          type: "video",
          frame: { topic: ch.info.topic, timeNs: t, format: m.format ?? "jpeg", data: copy },
        } satisfies WorkerOutbound,
        [copy.buffer],
      );
    } else if (ch.info.kind === "twist") {
      const m = decoded as {
        linear: { x: number; y: number; z: number };
        angular: { x: number; y: number; z: number };
      };
      ctx.postMessage({
        type: "twist",
        msg: {
          topic: ch.info.topic, timeNs: t,
          linearX: m.linear.x, linearY: m.linear.y, linearZ: m.linear.z,
          angularX: m.angular.x, angularY: m.angular.y, angularZ: m.angular.z,
        },
      } satisfies WorkerOutbound);
    } else if (ch.info.kind === "tf") {
      const m = decoded as {
        transforms: Array<{
          header: { frame_id: string };
          child_frame_id: string;
          transform: {
            translation: { x: number; y: number; z: number };
            rotation: { x: number; y: number; z: number; w: number };
          };
        }>;
      };
      const t0 = m.transforms?.[0];
      if (t0) {
        ctx.postMessage({
          type: "tf",
          msg: {
            topic: ch.info.topic, timeNs: t,
            parentFrame: t0.header.frame_id,
            childFrame: t0.child_frame_id,
            x: t0.transform.translation.x,
            y: t0.transform.translation.y,
            z: t0.transform.translation.z,
            qx: t0.transform.rotation.x,
            qy: t0.transform.rotation.y,
            qz: t0.transform.rotation.z,
            qw: t0.transform.rotation.w,
          },
        } satisfies WorkerOutbound);
      }
    } else if (ch.info.kind === "joints") {
      const m = decoded as {
        name?: string[];
        position?: ArrayLike<number>;
        velocity?: ArrayLike<number>;
        effort?: ArrayLike<number>;
      };
      ctx.postMessage({
        type: "joints",
        msg: {
          topic: ch.info.topic,
          timeNs: t,
          names: m.name ?? [],
          position: m.position ? Array.from(m.position, Number) : [],
          velocity: m.velocity ? Array.from(m.velocity, Number) : [],
          effort: m.effort ? Array.from(m.effort, Number) : [],
        },
      } satisfies WorkerOutbound);
    } else if (ch.info.kind === "other") {
      const topic = ch.info.topic;

      // P1.7.5 — forward the full decoded message for the JSON
      // inspector. Cap per topic to bound memory: at most
      // OTHER_MSGS_CAP_PER_TOPIC most-recent messages are kept on
      // the main thread (older ones drop via a windowed buffer
      // there). We don't drop here — we send and let the main
      // thread enforce the window — because the inspector wants
      // the message at the playhead, which might be anywhere.
      ctx.postMessage({
        type: "other",
        msg: { topic, timeNs: t, decoded: cloneSafe(decoded) },
      } satisfies WorkerOutbound);

      // P1.7.4 — generic scalar extraction. On the first message we
      // probe for a useful numeric leaf and remember the field path;
      // on subsequent messages we walk that same path. If the first
      // probe returns nothing, mark the topic "none" so we don't keep
      // re-probing every message.
      let fp = scalarFieldByTopic.get(topic);
      if (fp === undefined) {
        const found = pickScalarField(decoded);
        if (found === null) {
          scalarFieldByTopic.set(topic, "none");
          continue;
        }
        fp = found.fieldPath;
        scalarFieldByTopic.set(topic, fp);
      }
      if (fp === "none") continue;
      const v = walkPath(decoded, fp);
      if (typeof v === "number" && Number.isFinite(v)) {
        ctx.postMessage({
          type: "scalar",
          msg: { topic, timeNs: t, fieldPath: fp, value: v },
        } satisfies WorkerOutbound);
      } else if (typeof v === "bigint") {
        const n = Number(v);
        if (Number.isFinite(n)) {
          ctx.postMessage({
            type: "scalar",
            msg: { topic, timeNs: t, fieldPath: fp, value: n },
          } satisfies WorkerOutbound);
        }
      }
    }
  }

  ctx.postMessage({ type: "done", counts } satisfies WorkerOutbound);
}

/** Walk a dotted field path on a decoded message. Returns the leaf value or undefined. */
function walkPath(obj: unknown, path: string): unknown {
  if (!path) return undefined;
  let cur: unknown = obj;
  for (const segment of path.split(".")) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[segment];
  }
  return cur;
}

/**
 * Recursively convert a decoded message into a structured-clone-safe
 * tree before postMessage:
 *   - BigInts → number when safely in JS-number range, otherwise string
 *   - TypedArrays → Array<number>
 *   - everything else passes through
 *
 * Without this, edge cases (very large BigInts, huge typed arrays) can
 * cause clone failures or be unergonomic to render in the inspector.
 */
function cloneSafe(v: unknown): unknown {
  if (v === null || v === undefined) return v;
  const t = typeof v;
  if (t === "bigint") {
    const n = Number(v as bigint);
    return Number.isSafeInteger(n) ? n : (v as bigint).toString();
  }
  if (t !== "object") return v;
  if (ArrayBuffer.isView(v)) {
    // TypedArray → number[] (capped to 64 entries; full data goes to
    // MCAP, the inspector only needs to show structure).
    const arr = Array.from(v as unknown as ArrayLike<number>);
    return arr.length > 64
      ? [...arr.slice(0, 64), `…${arr.length - 64} more`]
      : arr;
  }
  if (Array.isArray(v)) {
    if (v.length > 64) {
      return [
        ...v.slice(0, 64).map(cloneSafe),
        `…${v.length - 64} more`,
      ];
    }
    return v.map(cloneSafe);
  }
  const out: Record<string, unknown> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    out[k] = cloneSafe(val);
  }
  return out;
}

ctx.addEventListener("message", (ev: MessageEvent<WorkerInbound>) => {
  const msg = ev.data;
  if (msg.type === "load") {
    loadAndDecode(msg.url).catch((e) => {
      console.error("MCAP decode failure", e);
      ctx.postMessage({
        type: "error",
        message: String(e?.message ?? e),
      } satisfies WorkerOutbound);
    });
  }
});

export type {};
