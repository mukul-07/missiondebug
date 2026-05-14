/**
 * v2 P1.7.6 — "Open in Foxglove Studio" deep-link button.
 *
 * Foxglove Studio (https://app.foxglove.dev) supports opening MCAP
 * files from a remote URL via the ?ds=remote-file&ds.url=<URL>
 * query string. Clicking the button opens Foxglove in a new tab,
 * pointed at this session's MCAP endpoint.
 *
 * Reinforces the standards-anchor wedge: for any topic MissionDebug
 * doesn't render natively, the engineer is one click away from the
 * de-facto ROS 2 visualization tool. We don't try to be Foxglove;
 * we hand off cleanly.
 */

type Props = {
  sessionId: string;
};

export function FoxgloveButton({ sessionId }: Props) {
  if (!sessionId) return null;

  // The hub's mcap endpoint is relative; Foxglove needs an absolute
  // URL it can fetch from the user's browser. Reconstruct from the
  // current page origin.
  const mcapUrl = `${window.location.origin}/api/sessions/${encodeURIComponent(sessionId)}/mcap`;
  const studioUrl = `https://app.foxglove.dev/?ds=remote-file&ds.url=${encodeURIComponent(mcapUrl)}`;

  return (
    <a
      href={studioUrl}
      target="_blank"
      rel="noopener noreferrer"
      title="Open this session's MCAP in Foxglove Studio"
      className="text-xs px-2 py-1 rounded border border-border text-muted hover:text-text hover:border-accent"
    >
      Open in Foxglove ↗
    </a>
  );
}
