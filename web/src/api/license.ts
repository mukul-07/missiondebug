/**
 * v2 EE — license status (the paid-tier lock).
 *
 * GET /api/admin/license reports whether a valid paid license is installed,
 * what it unlocks, and over-deployment (robots reporting vs the licensed cap,
 * computed locally on the hub — no phone-home, so it works air-gapped).
 * "licensed: false" = the free Community tier.
 */

export interface LicenseStatus {
  licensed: boolean;
  customer: string | null;
  robots: number; // licensed cap; 0 = unlimited
  features: string[];
  expires_at: number | null; // epoch seconds
  license_id: string | null; // the watermark
  robots_active: number;
  over_limit: boolean;
}

export async function getLicenseStatus(): Promise<LicenseStatus> {
  const r = await fetch("/api/admin/license");
  if (!r.ok) throw new Error(`getLicenseStatus: ${r.status}`);
  return r.json();
}
