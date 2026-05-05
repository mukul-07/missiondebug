import * as React from "react";

type Props = {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
};

export function Dialog({ open, onClose, children }: Props) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-border rounded p-4 max-w-lg w-full"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}
