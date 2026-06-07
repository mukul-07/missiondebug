// Shared types between main thread and the MCAP-decoder Web Worker.

export type ChannelKind = "video" | "twist" | "tf" | "joints" | "other";

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
 * Manipulator joint state. A `sensor_msgs/JointState` carries parallel
 * arrays — `name[]` plus `position[]`/`velocity[]`/`effort[]` — which the
 * generic scalar walker skips (arrays need an index). This dedicated decode
 * keeps them so the arm's per-joint motion can be charted (one line per
 * joint), instead of falling through to "no chart". Arrays may be shorter
 * than `name` (e.g. position-only messages) — the renderer handles that.
 */
export interface DecodedJoints {
  topic: string;
  timeNs: bigint;
  names: string[];
  position: number[];
  velocity: number[];
  effort: number[];
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

/**
 * v2 P1.7.5 — full decoded message for the JSON inspector.
 *
 * For "other" topics we also send the full decoded message so the
 * SessionDetail can render a tree view synced to the playhead.
 * Memory cost: bounded by the cap in mcap-decoder.ts (keeps at most
 * N most recent messages per topic; older ones drop).
 *
 * `decoded` is whatever @foxglove/rosmsg2-serialization produced —
 * a plain JS object with numbers, strings, bigints, and (rarely)
 * typed arrays. structuredClone'd across the worker boundary.
 */
export interface DecodedOther {
  topic: string;
  timeNs: bigint;
  decoded: unknown;
}

export type WorkerInbound = { type: "load"; url: string };

export type WorkerOutbound =
  | { type: "channels"; channels: ChannelInfo[]; startNs: string; endNs: string }
  | { type: "video"; frame: DecodedVideoFrame }
  | { type: "twist"; msg: DecodedTwist }
  | { type: "tf"; msg: DecodedTf }
  | { type: "joints"; msg: DecodedJoints }
  | { type: "scalar"; msg: DecodedScalar }
  | { type: "other"; msg: DecodedOther }
  | { type: "done"; counts: Record<string, number> }
  | { type: "error"; message: string };
