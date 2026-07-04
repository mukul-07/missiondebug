/**
 * Hub meta flags. `demo_tour` is set only by the demo image
 * (MD_DEMO_TOUR=1) so the UI can show its guided-tour strip.
 */

export interface HubMeta {
  ok: boolean;
  demo_tour?: boolean;
}

export async function getHubMeta(): Promise<HubMeta> {
  const r = await fetch("/healthz");
  if (!r.ok) return { ok: false };
  return r.json();
}
