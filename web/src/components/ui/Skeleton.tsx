import * as React from "react";

/**
 * Animated placeholder used during `useQuery` pending states. Drop-in
 * replacement for the old "Loading…" strings.
 *
 *   <Skeleton className="h-4 w-32" />
 *   <SkeletonCard />
 */
export function Skeleton({
  className = "",
  style,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded bg-border ${className}`}
      style={{
        animation: "md-skeleton-shimmer 1.4s ease-in-out infinite",
        ...style,
      }}
      aria-hidden="true"
      {...rest}
    />
  );
}

/** A session-list card placeholder. Matches the real card's vertical rhythm. */
export function SkeletonCard() {
  return (
    <div className="bg-panel border border-border rounded p-3">
      <div className="flex items-center justify-between">
        <div className="flex-1 grid gap-2">
          <Skeleton className="h-4 w-48" />
          <div className="flex gap-2">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-24" />
          </div>
        </div>
        <Skeleton className="h-3 w-14" />
      </div>
    </div>
  );
}

/** Vertical stack of N session-card skeletons. Used by SessionList. */
export function SkeletonSessionList({ count = 5 }: { count?: number }) {
  return (
    <div className="grid gap-2">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
