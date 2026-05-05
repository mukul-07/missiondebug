import { create } from "zustand";

export interface PlaybackState {
  currentTimeNs: bigint;
  durationNs: bigint;
  startNs: bigint; // wall-clock ns of session start; for human-readable mapping
  isPlaying: boolean;
  speed: number;
  setDuration: (start: bigint, durationNs: bigint) => void;
  setTime: (t: bigint) => void;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  step: (deltaNs: bigint) => void;
  setSpeed: (s: number) => void;
}

export const usePlayback = create<PlaybackState>((set, get) => ({
  currentTimeNs: 0n,
  durationNs: 0n,
  startNs: 0n,
  isPlaying: false,
  speed: 1,
  setDuration: (start, durationNs) => set({ startNs: start, durationNs, currentTimeNs: 0n }),
  setTime: (t) => {
    const { durationNs } = get();
    const clamped = t < 0n ? 0n : t > durationNs ? durationNs : t;
    set({ currentTimeNs: clamped });
  },
  play: () => set({ isPlaying: true }),
  pause: () => set({ isPlaying: false }),
  toggle: () => set((s) => ({ isPlaying: !s.isPlaying })),
  step: (deltaNs) => {
    const { currentTimeNs } = get();
    get().setTime(currentTimeNs + deltaNs);
  },
  setSpeed: (s) => set({ speed: s }),
}));
