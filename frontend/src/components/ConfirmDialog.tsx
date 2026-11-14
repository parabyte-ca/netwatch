import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

export interface Confirm {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  run: () => Promise<void>;
}

/** A modal that says exactly what will change and runs it only on an explicit click. Esc or
 * Cancel closes it without doing anything; focus stays inside while it is open. */
export function ConfirmDialog({ confirm, onClose }: { confirm: Confirm; onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    cancel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
      if (e.key === 'Tab' && box.current) {
        const items = [...box.current.querySelectorAll<HTMLElement>('button:not([disabled])')];
        if (!items.length) return;
        const first = items[0]!, last = items[items.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [busy, onClose]);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await confirm.run();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div ref={box} role="dialog" aria-modal="true" aria-labelledby={titleId}
        className="w-full max-w-md space-y-3 rounded-lg bg-slate-900 p-5 shadow-xl ring-1 ring-slate-700">
        <h2 id={titleId} className="text-base font-semibold text-white">{confirm.title}</h2>
        <div className="space-y-2 text-sm text-slate-300">{confirm.body}</div>
        {error && <p className="text-sm text-rose-300" role="alert">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button ref={cancel} onClick={onClose} disabled={busy}
            className="rounded-md px-3 py-1.5 text-sm text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800 disabled:opacity-50">
            Cancel
          </button>
          <button onClick={() => void go()} disabled={busy}
            className={`rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 ${confirm.danger ? 'bg-rose-600 hover:bg-rose-500' : 'bg-sky-600 hover:bg-sky-500'}`}>
            {busy ? 'Working…' : confirm.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
