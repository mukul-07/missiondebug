// Binary search the nearest entry by timestamp. Inputs assumed time-sorted.

export function nearestByTime<T extends { timeNs: bigint }>(
  arr: T[],
  startNs: bigint,
  offsetNs: bigint,
): T | null {
  if (arr.length === 0) return null;
  const target = startNs + offsetNs;
  let lo = 0;
  let hi = arr.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid].timeNs < target) lo = mid + 1;
    else hi = mid;
  }
  // arr[lo] is the first entry >= target (or last entry).
  if (lo === 0) return arr[0];
  const a = arr[lo - 1];
  const b = arr[lo];
  return abs(a.timeNs - target) <= abs(b.timeNs - target) ? a : b;
}

function abs(x: bigint): bigint {
  return x < 0n ? -x : x;
}
