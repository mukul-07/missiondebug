import * as React from "react";

/**
 * Catches render errors anywhere below it and shows a recoverable card
 * instead of a blank white page. Wrap routes with this so a busted
 * session detail doesn't crash the whole shell.
 */
interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<
  React.PropsWithChildren,
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Surfaced to the console so devs can find it; not sent anywhere.
    console.error("ErrorBoundary caught:", error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    const err = this.state.error;
    if (err === null) return this.props.children;
    return (
      <div className="p-6">
        <div className="bg-panel border border-border rounded p-6 grid gap-3 max-w-2xl">
          <div className="text-accent text-sm uppercase tracking-wide">
            Something broke
          </div>
          <div className="font-mono text-sm break-words">{err.message}</div>
          <div className="text-xs text-muted">
            The rest of the app is still working. Reload the page or click
            below to clear the error and retry.
          </div>
          <div className="flex gap-2 mt-2">
            <button
              type="button"
              onClick={this.reset}
              className="px-3 py-1 rounded border border-border hover:border-accent text-sm"
            >
              Retry
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="px-3 py-1 rounded bg-accent text-bg text-sm"
            >
              Reload page
            </button>
          </div>
        </div>
      </div>
    );
  }
}
