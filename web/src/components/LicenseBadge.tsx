/**
 * Compact license/edition indicator. Shows "Community (free)" on an
 * unlicensed hub, or "Licensed · <customer> · N/M robots" when a paid key is
 * installed — turning amber if more robots are reporting than the license
 * allows (over-deployment). Renders nothing while loading or on error, so it
 * never disrupts the page.
 */

import { useQuery } from "@tanstack/react-query";
import { getLicenseStatus } from "../api/license";

export function LicenseBadge() {
  const { data } = useQuery({
    queryKey: ["license"],
    queryFn: getLicenseStatus,
    staleTime: 60_000,
  });
  if (!data) return null;

  if (!data.licensed) {
    return (
      <span
        className="text-xs text-muted border border-border rounded px-2 py-0.5"
        title="Free Community tier (MIT). Paid features need a license key."
      >
        Community · free
      </span>
    );
  }

  const robots =
    data.robots > 0
      ? `${data.robots_active}/${data.robots} robots`
      : `${data.robots_active} robots`;
  const cls = data.over_limit
    ? "border-accent text-accent"
    : "border-border text-muted";
  const title = data.over_limit
    ? `Over your license: ${data.robots_active} robots reporting, ${data.robots} licensed`
    : `Licensed${data.customer ? ` to ${data.customer}` : ""}`;

  return (
    <span className={`text-xs rounded px-2 py-0.5 border ${cls}`} title={title}>
      {data.over_limit ? "⚠ " : ""}
      Licensed{data.customer ? ` · ${data.customer}` : ""} · {robots}
    </span>
  );
}
