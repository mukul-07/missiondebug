import { Link, Route, Routes } from "react-router-dom";
import { SessionList } from "./components/SessionList";
import { SessionDetail } from "./components/SessionDetail";
import { CommandPalette } from "./components/CommandPalette";
import { ErrorBoundary } from "./components/ui/ErrorBoundary";
import { ThemeToggle } from "./components/ui/ThemeToggle";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border px-4 py-2 flex items-center gap-3">
        <Link to="/" className="font-semibold">
          MissionDebug <span className="text-muted text-xs">v0</span>
        </Link>
        <span className="text-muted text-xs">localhost</span>
        <div className="ml-auto flex items-center gap-2">
          <span
            className="text-[10px] text-muted border border-border rounded px-1.5 py-0.5"
            title="Press cmd+K (or ctrl+K) to jump"
          >
            ⌘K
          </span>
          <ThemeToggle />
        </div>
      </header>
      <main className="flex-1">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<SessionList />} />
            <Route path="/sessions/:id" element={<SessionDetail />} />
          </Routes>
        </ErrorBoundary>
      </main>
      <CommandPalette />
    </div>
  );
}
