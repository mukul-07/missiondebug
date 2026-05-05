/// <reference lib="webworker" />
// MCAP-decoder Web Worker.
//
// Streams an MCAP file with @mcap/core, registers ROS 2 schemas, and
// deserializes Twist / TF / CompressedImage / nav_msgs Path with
// @foxglove/rosmsg2-serialization. Posts decoded messages back to the
// main thread bucketed by topic.

import { MessageReader } from "@foxglove/rosmsg2-serialization";
import { parse as parseRos2Schema } from "@foxglove/rosmsg";
import { McapStreamReader } from "@mcap/core";
import { decompress as zstdDecompress } from "fzstd";
import type {
  ChannelInfo,
  ChannelKind,
  WorkerInbound,
  WorkerOutbound,
} from "./types";

const decompressHandlers = {
  zstd: (data: Uint8Array, decompressedSize: bigint) => {
    const out = new Uint8Array(Number(decompressedSize));
    zstdDecompress(data, out);
    return out;
  },
};

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope;

function classify(schemaName: string): ChannelKind {
  if (schemaName.endsWith("CompressedImage")) return "video";
  if (schemaName.endsWith("Twist")) return "twist";
  if (schemaName.endsWith("TFMessage")) return "tf";
  return "other";
}

interface Channel {
  info: ChannelInfo;
  reader: MessageReader | null;
}

async function loadAndDecode(url: string): Promise<void> {
  const resp = await fetch(url);
  if (!resp.ok || !resp.body) throw new Error(`fetch failed: ${resp.status}`);

  const reader = new McapStreamReader({ decompressHandlers });
  const schemasById = new Map<number, { name: string; encoding: string; data: Uint8Array }>();
  const channels = new Map<number, Channel>();
  const counts: Record<string, number> = {};
  let firstNs: bigint | null = null;
  let lastNs: bigint = 0n;
  let channelsAnnounced = false;

  const announceChannels = () => {
    const announce: WorkerOutbound = {
      type: "channels",
      channels: Array.from(channels.values()).map((c) => c.info),
      startNs: (firstNs ?? 0n).toString(),
      endNs: lastNs.toString(),
    };
    ctx.postMessage(announce);
  };

  const buildReader = (schemaName: string, schemaText: string): MessageReader | null => {
    try {
      const defs = parseRos2Schema(schemaText, { ros2: true });
      return new MessageReader(defs);
    } catch (e) {
      console.warn("schema parse failed", schemaName, e);
      return null;
    }
  };

  const flush = () => {
    let record;
    while ((record = reader.nextRecord())) {
      switch (record.type) {
        case "Schema":
          schemasById.set(record.id, {
            name: record.name,
            encoding: record.encoding,
            data: record.data,
          });
          break;
        case "Channel": {
          const schema = schemasById.get(record.schemaId);
          const schemaName = schema?.name ?? "";
          let msgReader: MessageReader | null = null;
          if (schema && schema.encoding === "ros2msg") {
            const text = new TextDecoder().decode(schema.data);
            msgReader = buildReader(schemaName, text);
          }
          const info: ChannelInfo = {
            id: record.id,
            topic: record.topic,
            schemaName,
            kind: classify(schemaName),
          };
          channels.set(record.id, { info, reader: msgReader });
          counts[record.topic] = 0;
          break;
        }
        case "Message": {
          const ch = channels.get(record.channelId);
          if (!ch) break;
          const t = record.logTime;
          if (firstNs === null) firstNs = t;
          if (t > lastNs) lastNs = t;
          counts[ch.info.topic] = (counts[ch.info.topic] ?? 0) + 1;

          if (!ch.reader) break;
          let decoded: unknown;
          try {
            decoded = ch.reader.readMessage(record.data);
          } catch (e) {
            console.warn("decode failed", ch.info.topic, e);
            break;
          }

          if (ch.info.kind === "video") {
            const m = decoded as { format: string; data: Uint8Array };
            ctx.postMessage(
              {
                type: "video",
                frame: {
                  topic: ch.info.topic,
                  timeNs: t,
                  format: m.format ?? "jpeg",
                  data: m.data,
                },
              } satisfies WorkerOutbound,
              [m.data.buffer],
            );
          } else if (ch.info.kind === "twist") {
            const m = decoded as {
              linear: { x: number; y: number; z: number };
              angular: { x: number; y: number; z: number };
            };
            ctx.postMessage({
              type: "twist",
              msg: {
                topic: ch.info.topic,
                timeNs: t,
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
                  topic: ch.info.topic,
                  timeNs: t,
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
          break;
        }
      }
    }
  };

  const stream = resp.body.getReader();
  while (true) {
    const { done, value } = await stream.read();
    if (done) break;
    if (value) {
      reader.append(value);
      flush();
      if (!channelsAnnounced && channels.size > 0 && firstNs !== null) {
        channelsAnnounced = true;
        announceChannels();
      }
    }
  }
  flush();

  if (firstNs !== null) announceChannels();

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
