// Shared types between main thread and the MCAP-decoder Web Worker.

export type ChannelKind = "video" | "twist" | "tf" | "other";

export interface ChannelInfo {
  id: number;
  topic: string;
  schemaName: string;
  kind: ChannelKind;
}

export interface DecodedVideoFrame {
  topic: string;
  timeNs: bigint;
  format: string; // e.g. "jpeg", "png"
  data: Uint8Array; // raw compressed bytes; transferable
}

export interface DecodedTwist {
  topic: string;
  timeNs: bigint;
  linearX: number;
  linearY: number;
  linearZ: number;
  angularX: number;
  angularY: number;
  angularZ: number;
}

export interface DecodedTf {
  topic: string;
  timeNs: bigint;
  // First transform's translation/rotation, simplified for v0.
  childFrame: string;
  parentFrame: string;
  x: number;
  y: number;
  z: number;
  qx: number;
  qy: number;
  qz: number;
  qw: number;
}

/**
 * v2 P1.7.4 — generic scalar sample.
 *
 * For any topic the worker doesn't have a dedicated decoder for (kind
 * === "other"), it walks the first decoded message to find a useful
 * numeric leaf via a heuristic field-picker (see `pickScalarField` in
 * mcap-decoder.ts) and emits one `scalar` event per subsequent message.
 *
 * The same `fieldPath` is used for every message on that topic — picked
 * once on first message, then reused. Customers can later override via
 * agent config; for now this auto-discovery gets you a chart per topic
 * on any 30-70 topic fleet without configuration.
 */
export interface DecodedScalar {
  topic: string;
  timeNs: bigint;
  fieldPath: string;
  value: number;
}

export type WorkerInbound = { type: "load"; url: string };

export type WorkerOutbound =
  | { type: "channels"; channels: ChannelInfo[]; startNs: string; endNs: string }
  | { type: "video"; frame: DecodedVideoFrame }
  | { type: "twist"; msg: DecodedTwist }
  | { type: "tf"; msg: DecodedTf }
  | { type: "scalar"; msg: DecodedScalar }
  | { type: "done"; counts: Record<string, number> }
  | { type: "error"; message: string };
