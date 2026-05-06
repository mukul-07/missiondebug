export interface Annotation {
  id: number;
  session_id: string;
  time_ns: number; // offset from session start
  body: string;
  created_at: number;
}

export async function listAnnotations(sessionId: string): Promise<Annotation[]> {
  const r = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/annotations`,
  );
  if (!r.ok) throw new Error(`listAnnotations: ${r.status}`);
  const j = await r.json();
  return j.annotations;
}

export async function createAnnotation(
  sessionId: string,
  timeNs: number,
  body: string,
): Promise<Annotation> {
  const r = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/annotations`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ time_ns: timeNs, body }),
    },
  );
  if (!r.ok) throw new Error(`createAnnotation: ${r.status}`);
  return r.json();
}

export async function deleteAnnotation(id: number): Promise<void> {
  const r = await fetch(`/api/annotations/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`deleteAnnotation: ${r.status}`);
}
