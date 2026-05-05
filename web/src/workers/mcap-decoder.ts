/// <reference lib="webworker" />
// MCAP-decoder Web Worker. Fetches a session MCAP, parses with @mcap/core,
// and streams decoded messages back to the main thread bucketed by kind.

import { McapStreamReader } from "@mcap/core";
import { ROS2_DATATYPE, parseChannel } from "@mcap/support";
import type {
  ChannelInfo,
  ChannelKind,
  WorkerInbound,
  WorkerOutbound,
} from "./types";

const ctx: DedicatedWorkerGlobalScope = self as unknown as DedicatedWorkerGlobalScope;

function classify(schemaName: string): ChannelKind {
  if (schemaName === "sensor_msgs/msg/CompressedImage") return "video";
  if (schemaName === "geometry_msgs/msg/Twist") return "twist";
  if (schemaName === "tf2_msgs/msg/TFMessage") return "tf";
  return "other";
}

async function loadAndDecode(url: string): Promise<void> {
  const resp = await fetch(url);
  if (!resp.ok || !resp.body) throw new Error(`fetch failed: ${resp.status}`);

  const reader = new McapStreamReader();
  const channels = new Map<number, { info: ChannelInfo; decode: (data: Uint8Array) => unknown }>();
  const counts: Record<string, number> = {};
  let firstNs: bigint | null = null;
  let lastNs: bigint = 0n;
  let channelsAnnounced = false;

  const stream = resp.body.getReader();

  const flushRecords = () => {
    let record;
    while ((record = reader.nextRecord())) {
      switch (record.type) {
        case "Schema":
          // we'll resolve schemas via parseChannel when we see channels
          break;
        case "Channel": {
          const schemaId = record.schemaId;
          const schema = reader.schemasById?.get?.(schemaId) ?? null;
          let schemaName = schema?.name ?? "";
          let decode: (d: Uint8Array) => unknown = () => ({});
          try {
            if (schema) {
              const parsed = parseChannel({
                messageEncoding: record.messageEncoding,
                schema: { ...schema, data: schema.data },
              } as Parameters<typeof parseChannel>[0]);
              decode = parsed.deserialize;
              schemaName = parsed.fullSchemaName ?? schemaName;
            }
          } catch (e) {
            // unknown schema; we'll still record but skip decode.
            console.warn("parseChannel failed", schemaName, e);
          }
          const info: ChannelInfo = {
            id: record.id,
            topic: record.topic,
            schemaName,
            kind: classify(schemaName),
          };
          channels.set(record.id, { info, decode });
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

          if (ch.info.kind === "video") {
            try {
              const m = ch.decode(record.data) as { format: string; data: Uint8Array };
              const out: WorkerOutbound = {
                type: "video",
                frame: {
                  topic: ch.info.topic,
                  timeNs: t,
                  format: m.format ?? "jpeg",
                  data: m.data,
                },
              };
              ctx.postMessage(out, [m.data.buffer]);
            } catch {
              /* skip */
            }
          } else if (ch.info.kind === "twist") {
            try {
              const m = ch.decode(record.data) as {
                linear: { x: number; y: number; z: number };
                angular: { x: number; y: number; z: number };
              };
              const out: WorkerOutbound = {
                type: "twist",
                msg: {
                  topic: ch.info.topic,
                  timeNs: t,
                  linearX: m.linear.x,
                  linearY: m.linear.y,
                  linearZ: m.linear.z,
                  angularX: m.angular.x,
                  angularY: m.angular.y,
                  angularZ: m.angular.z,
                },
              };
              ctx.postMessage(out);
            } catch {
              /* skip */
            }
          } else if (ch.info.kind === "tf") {
            try {
              const m = ch.decode(record.data) as {
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
                const out: WorkerOutbound = {
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
                };
                ctx.postMessage(out);
              }
            } catch {
              /* skip */
            }
          }
          break;
        }
      }
    }
  };

  // Stream the body in.
  while (true) {
    const { done, value } = await stream.read();
    if (done) break;
    if (value) {
      reader.append(value);
      flushRecords();
      if (!channelsAnnounced && channels.size > 0 && firstNs !== null) {
        channelsAnnounced = true;
        const announce: WorkerOutbound = {
          type: "channels",
          channels: Array.from(channels.values()).map((c) => c.info),
          startNs: firstNs.toString(),
          endNs: lastNs.toString(),
        };
        ctx.postMessage(announce);
      }
    }
  }
  flushRecords();

  // Re-announce in case the channels were not present until late.
  if (firstNs !== null) {
    const announce: WorkerOutbound = {
      type: "channels",
      channels: Array.from(channels.values()).map((c) => c.info),
      startNs: firstNs.toString(),
      endNs: lastNs.toString(),
    };
    ctx.postMessage(announce);
  }

  ctx.postMessage({ type: "done", counts } as WorkerOutbound);
}

ctx.addEventListener("message", (ev: MessageEvent<WorkerInbound>) => {
  const msg = ev.data;
  if (msg.type === "load") {
    loadAndDecode(msg.url).catch((e) => {
      const out: WorkerOutbound = { type: "error", message: String(e?.message ?? e) };
      ctx.postMessage(out);
    });
  }
});

// Pacify TS — referenced types
export type {};

// Minor helper: McapStreamReader stores schemas internally; expose for our use.
declare module "@mcap/core" {
  interface McapStreamReader {
    schemasById?: Map<number, { name: string; encoding: string; data: Uint8Array }>;
  }
}

// Touch the constant so unused-import lint doesn't complain.
void ROS2_DATATYPE;
