import * as React from "react";

type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "ghost";
};

export function Button({ variant = "default", className = "", ...rest }: Props) {
  const base = "px-3 py-1.5 rounded text-sm transition-colors";
  const styles =
    variant === "ghost"
      ? "bg-transparent hover:bg-panel border border-border"
      : "bg-accent text-white hover:opacity-90";
  return <button className={`${base} ${styles} ${className}`} {...rest} />;
}
