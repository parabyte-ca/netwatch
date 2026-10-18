import { Component, type ErrorInfo, type ReactNode } from 'react';

/** Shows what broke instead of a blank page, with a way back. */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string; compact?: boolean }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`NetWatch ${this.props.label ?? 'page'} crashed`, error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.compact) {
      return (
        <div role="alert" className="fixed bottom-4 right-4 z-50 max-w-xs rounded-lg bg-rose-950 p-3 text-sm text-rose-200 ring-1 ring-rose-800">
          {this.props.label ?? 'This part'} hit an error: {error.message}{' '}
          <button className="underline" onClick={() => this.setState({ error: null })}>Close</button>
        </div>
      );
    }
    return (
      <div role="alert" className="mx-auto mt-16 max-w-lg rounded-lg bg-slate-900 p-6 text-sm text-slate-300 ring-1 ring-rose-800">
        <h1 className="mb-2 text-lg font-semibold text-white">Something went wrong on this page</h1>
        <p className="mb-3">NetWatch is still running; only this page failed to draw. Please send this message if it keeps happening:</p>
        <pre className="mb-4 overflow-x-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-2 text-xs text-rose-300">{error.message}</pre>
        <div className="flex gap-3">
          <button className="rounded-md bg-sky-600 px-3 py-1.5 font-medium text-white hover:bg-sky-500" onClick={() => window.location.reload()}>Reload</button>
          <button className="rounded-md px-3 py-1.5 text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800" onClick={() => { this.setState({ error: null }); window.location.assign('/'); }}>Go to Overview</button>
        </div>
      </div>
    );
  }
}
