const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';

/** Saved but polling unticked: say what that costs, with a one-click fix. */
export function NotPolling({ state, needs, busy, onEnable }: { state?: string; needs: string; busy: boolean; onEnable: () => void }) {
  if (state !== 'not_polling') return null;
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 rounded-md bg-amber-950/40 p-2 text-xs text-amber-200 ring-1 ring-amber-900">
      <span className="flex-1">Saved, but not polling. {needs} need polling on.</span>
      <button className={`${btn} bg-amber-600 text-white hover:bg-amber-500`} disabled={busy} onClick={onEnable}>Turn on polling and save</button>
    </div>
  );
}
