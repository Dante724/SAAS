import { Component, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

// One screen crashing (a render error, or a chunk that failed to download after a deploy) used to
// unmount the whole tree and leave a blank page. This catches it at the route, shows what happened
// with a way out, and clears itself when the user goes to another address.

type Props = { resetKey: string; children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error("Screen crashed:", error, info?.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    // A lazy chunk that 404s after a new deploy: only a reload fetches the new file names.
    const stale = /dynamically imported module|Failed to fetch|Importing a module script failed|ChunkLoadError/i.test(error.message || "");
    return (
      <div className="min-h-[60vh] grid place-items-center p-10 text-center" role="alert">
        <div className="max-w-sm">
          <div className="text-sm font-semibold text-ink-primary">
            {stale ? "A new version of Tekly is available" : "This screen hit a problem"}
          </div>
          <div className="text-xs text-ink-tertiary mt-1">
            {stale ? "Reload to get it. Nothing you saved is lost." : "Your saved work is safe. Reload the page, or go back and try again."}
          </div>
          {!stale && error.message && (
            <div className="text-[11px] text-ink-tertiary mt-2 break-words">{error.message}</div>
          )}
          <div className="mt-4 flex items-center justify-center gap-2">
            <button type="button" onClick={() => window.location.reload()}
              className="px-3.5 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary-container">Reload</button>
            <button type="button" onClick={() => window.history.back()}
              className="px-3.5 py-2 rounded-lg border border-slate-200 bg-white text-ink-secondary text-xs font-semibold hover:bg-slate-50">Go back</button>
            <a href="/" className="px-3.5 py-2 rounded-lg text-ink-secondary text-xs font-semibold hover:bg-slate-50">Home</a>
          </div>
        </div>
      </div>
    );
  }
}

/** Route-level boundary: resets whenever the path changes. */
export default function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary resetKey={pathname}>{children}</ErrorBoundary>;
}
