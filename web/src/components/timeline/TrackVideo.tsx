import { useEffect, useRef } from "react";
import { usePlayback } from "../../stores/playback";
import { nearestByTime } from "../../hooks/useNearestByTime";
import type { DecodedVideoFrame } from "../../workers/types";

type Props = {
  label: string;
  frames: DecodedVideoFrame[];
};

export function TrackVideo({ label, frames }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const lastTimeRef = useRef<bigint>(-1n);
  const startNs = usePlayback((s) => s.startNs);
  const currentTimeNs = usePlayback((s) => s.currentTimeNs);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const frame = nearestByTime(frames, startNs, currentTimeNs);
    if (!frame) return;
    if (frame.timeNs === lastTimeRef.current) return;
    lastTimeRef.current = frame.timeNs;

    const blob = new Blob([frame.data as BlobPart], { type: `image/${frame.format}` });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  }, [frames, startNs, currentTimeNs]);

  return (
    <div className="bg-panel border border-border rounded p-2">
      <div className="text-xs text-muted mb-1">{label}</div>
      <canvas
        ref={canvasRef}
        className="w-full bg-black rounded"
        style={{ aspectRatio: "16/9", maxHeight: 240 }}
      />
    </div>
  );
}
