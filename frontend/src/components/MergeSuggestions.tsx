import { useState } from 'react';
import { api, type MergeSuggestion } from '../lib/api';
import { fmtAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';

/** "Same machine?": a device that just appeared next to an older one that went quiet, such as a
 * server after a network card swap. Merge keeps the older device's name, group, tags and notes. */
export function MergeSuggestions({ onDone }: { onDone: () => Promise<void> }) {
  const q = usePolling(api.mergeSuggestions, 120_000);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const items = Array.isArray(q.data) ? q.data : [];
  if (items.length === 0) return null;
  const now = Date.now();

  async function run(key: string, act: () => Promise<unknown>) {
    setBusy(key);
    setErr(null);
    try {
      await act();
      await Promise.all([q.refresh(), onDone()]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-2 rounded-lg bg-amber-950/30 p-3 ring-1 ring-amber-900" aria-label="Same machine?">
      <h2 className="text-sm font-semibold text-amber-100">Same machine?</h2>
      <p className="text-xs text-slate-400">
        A new network card has its own address, so NetWatch lists the machine again. Merge keeps the older entry (its name, group, tags and notes) and adds the new card to it.
      </p>
      <ul className="space-y-2">
        {items.map((s: MergeSuggestion) => {
          const key = `${s.new.id}:${s.old.id}`;
          return (
            <li key={key} className="space-y-1.5 rounded-md bg-slate-900 p-3 text-sm ring-1 ring-slate-800">
              <p className="text-slate-200">
                <span className="font-medium text-white">{s.new.label}</span> (online now{s.new.last_ip ? `, ${s.new.last_ip}` : ''}) looks like{' '}
                <span className="font-medium text-white">{s.old.label}</span> (last seen {fmtAgo(s.old.last_seen_ms, now)}
                {s.old.last_ip ? `, ${s.old.last_ip}` : ''}).
              </p>
              <p className="text-xs text-slate-400">Why: {s.reasons.join(' and ')}.</p>
              {s.both_monitored ? (
                <p className="text-xs text-amber-300">Both are monitored. Stop monitoring one of them on its page first, then merge.</p>
              ) : (
                <p className="text-xs text-slate-400">{s.old.label} stays.</p>
              )}
              <div className="flex flex-wrap gap-2">
                {!s.both_monitored && (
                  <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy === key}
                    aria-label={`Merge ${s.new.label} into ${s.old.label}`}
                    onClick={() => void run(key, () => api.mergeDevice(s.new.id, s.old.id))}>
                    Merge
                  </button>
                )}
                <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} disabled={busy === key}
                  aria-label={`${s.new.label} and ${s.old.label} are not the same`}
                  onClick={() => void run(key, () => api.dismissMergeSuggestion(s.new.id, s.old.id))}>
                  Not the same
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      {err && <p className="text-xs text-rose-300" role="alert">{err}</p>}
    </section>
  );
}
