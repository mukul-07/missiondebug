import * as React from "react";

export function Card({ className = "", ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`bg-panel border border-border rounded p-3 ${className}`}
      {...rest}
    />
  );
}
