import { useEffect, useState } from "react";

/**
 * Single toggle that flips <html class="light"> on/off and persists choice
 * to localStorage. Default is dark (preserves v1.5 look). Respects the
 * user's system preference on first load if they haven't chosen yet.
 *
 * Color tokens are CSS variables in index.css, so flipping the class is
 * all that's needed — no component code knows about themes.
 */

type Theme = "dark" | "light";
const STORAGE_KEY = "missiondebug:theme";

function readInitial(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function apply(theme: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("light", theme === "light");
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readInitial);

  useEffect(() => {
    apply(theme);
    window.localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="text-xs px-1.5 py-0.5 rounded border border-border text-muted hover:text-text hover:border-accent"
    >
      {theme === "dark" ? "☾ dark" : "☀ light"}
    </button>
  );
}
