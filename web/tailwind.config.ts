import type { Config } from "tailwindcss";

/**
 * Tailwind config — colors are wired to CSS variables in index.css so the
 * theme can be switched at runtime by toggling `class="light"` on <html>.
 * No component needs to know about themes; everything keeps using
 * `bg-bg`, `bg-panel`, `text-muted`, etc.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        panel: "var(--color-panel)",
        border: "var(--color-border)",
        text: "var(--color-text)",
        muted: "var(--color-muted)",
        accent: "var(--color-accent)",
      },
    },
  },
  plugins: [],
} satisfies Config;
