import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d10",
        panel: "#13161b",
        border: "#23272e",
        text: "#e7e9ee",
        muted: "#7d8590",
        accent: "#ff5a5f",
      },
    },
  },
  plugins: [],
} satisfies Config;
