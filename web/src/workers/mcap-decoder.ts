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
  return "other";
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
    }
  }

  ctx.postMessage({ type: "done", counts } satisfies WorkerOutbound);
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
