import * as React from "react";

/**
 * Empty-list / no-data placeholder. Consistent voice across the app for:
 *   - "no sessions yet"
 *   - "no annotations on this session"
 *   - "no agents reporting"
 *   - "no sessions match this filter"
 *
 * Usage:
 *   <EmptyState
 *     title="No sessions yet"
 *     description="Start the agent and trigger an anomaly to capture one."
 *     action={<Button onClick={...}>Capture now</Button>}
 *   />
 */
export interface EmptyStateProps {
  title: string;
  description?: React.ReactNode;
  /** Optional decorative emoji or icon node shown above the title. */
  icon?: React.ReactNode;
  /** Optional CTA — typically a single Button. */
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={`bg-panel border border-border rounded p-6 grid gap-2 place-items-center text-center ${className}`}
      role="status"
    >
      {icon ? <div className="text-2xl text-muted mb-1">{icon}</div> : null}
      <div className="text-base">{title}</div>
      {description ? (
        <div className="text-sm text-muted max-w-md">{description}</div>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
