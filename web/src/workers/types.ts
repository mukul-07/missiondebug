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

export type WorkerInbound = { type: "load"; url: string };

export type WorkerOutbound =
  | { type: "channels"; channels: ChannelInfo[]; startNs: string; endNs: string }
  | { type: "video"; frame: DecodedVideoFrame }
  | { type: "twist"; msg: DecodedTwist }
  | { type: "tf"; msg: DecodedTf }
  | { type: "done"; counts: Record<string, number> }
  | { type: "error"; message: string };
