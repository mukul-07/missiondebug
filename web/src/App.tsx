import { Link, Route, Routes } from "react-router-dom";
import { SessionList } from "./components/SessionList";
import { SessionDetail } from "./components/SessionDetail";
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
        <div className="ml-auto">
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
    </div>
  );
}
