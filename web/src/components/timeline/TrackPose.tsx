import { usePlayback } from "../../stores/playback";
import { nearestByTime } from "../../hooks/useNearestByTime";
import type { DecodedTf } from "../../workers/types";

type Props = { msgs: DecodedTf[] };

function quatToYaw(qx: number, qy: number, qz: number, qw: number): number {
  // Z-axis rotation (yaw) from quaternion.
  const siny = 2 * (qw * qz + qx * qy);
  const cosy = 1 - 2 * (qy * qy + qz * qz);
  return Math.atan2(siny, cosy);
}

export function TrackPose({ msgs }: Props) {
  const startNs = usePlayback((s) => s.startNs);
  const currentTimeNs = usePlayback((s) => s.currentTimeNs);
  const m = nearestByTime(msgs, startNs, currentTimeNs);
  if (!m) {
    return (
      <div className="bg-panel border border-border rounded p-2 text-xs text-muted">
        Pose: (no data)
      </div>
    );
  }
  const yaw = quatToYaw(m.qx, m.qy, m.qz, m.qw);
  return (
    <div className="bg-panel border border-border rounded p-2 font-mono text-xs">
      <div className="text-muted">
        {m.parentFrame} → {m.childFrame}
      </div>
      <div>x: {m.x.toFixed(3)}</div>
      <div>y: {m.y.toFixed(3)}</div>
      <div>z: {m.z.toFixed(3)}</div>
      <div>yaw: {yaw.toFixed(3)} rad</div>
    </div>
  );
}
