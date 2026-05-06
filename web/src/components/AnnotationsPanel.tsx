import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type Annotation,
  createAnnotation,
  deleteAnnotation,
  listAnnotations,
} from "../api/annotations";
import { usePlayback } from "../stores/playback";
import { Button } from "./ui/Button";

type Props = {
  sessionId: string;
};

function fmtTime(ns: number): string {
  return `${(ns / 1e9).toFixed(2)}s`;
}

export function AnnotationsPanel({ sessionId }: Props) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [composing, setComposing] = useState(false);

  const { data: annotations = [] } = useQuery({
    queryKey: ["annotations", sessionId],
    queryFn: () => listAnnotations(sessionId),
  });

  const create = useMutation({
    mutationFn: (params: { timeNs: number; body: string }) =>
      createAnnotation(sessionId, params.timeNs, params.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["annotations", sessionId] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
      setDraft("");
      setComposing(false);
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => deleteAnnotation(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["annotations", sessionId] });
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const onSave = () => {
    if (!draft.trim()) return;
    const t = Number(usePlayback.getState().currentTimeNs);
    create.mutate({ timeNs: t, body: draft.trim() });
  };

  const seekTo = (timeNs: number) => {
    usePlayback.getState().setTime(BigInt(timeNs));
  };

  return (
    <div className="bg-panel border border-border rounded p-2 text-xs">
      <div className="flex items-center justify-between mb-2">
        <div className="font-semibold">Notes ({annotations.length})</div>
        {!composing && (
          <Button
            variant="ghost"
            onClick={() => setComposing(true)}
            className="text-xs"
          >
            + Add at playhead
          </Button>
        )}
      </div>

      {composing && (
        <div className="mb-2">
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="What did you observe?"
            className="w-full bg-bg border border-border rounded p-1 text-xs"
            rows={3}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                onSave();
              }
              if (e.key === "Escape") {
                setComposing(false);
                setDraft("");
              }
            }}
          />
          <div className="flex gap-2 mt-1">
            <Button onClick={onSave} className="text-xs" disabled={create.isPending}>
              Save
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setComposing(false);
                setDraft("");
              }}
              className="text-xs"
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {annotations.length === 0 && !composing ? (
        <div className="text-muted">No notes on this session yet.</div>
      ) : (
        <ul className="grid gap-1">
          {annotations.map((a: Annotation) => (
            <li
              key={a.id}
              className="bg-bg border border-border rounded p-1.5 flex items-start justify-between gap-2"
            >
              <div className="min-w-0">
                <button
                  className="font-mono text-accent hover:underline"
                  onClick={() => seekTo(a.time_ns)}
                  title="Jump playhead"
                >
                  {fmtTime(a.time_ns)}
                </button>
                <div className="whitespace-pre-wrap break-words">{a.body}</div>
              </div>
              <button
                aria-label="Delete"
                className="text-muted hover:text-accent"
                onClick={() => remove.mutate(a.id)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
