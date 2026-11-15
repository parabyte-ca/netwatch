import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type HealthState, type IntegrationRow } from '../lib/api';
import { fmtAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const STATE: Record<HealthState, { label: string; dot: string; text: string }> = {
  ok: { label: 'OK', dot: 'bg-emerald-400', text: 'text-emerald-300' },
  waiting: { label: 'Starting', dot: 'bg-sky-400', text: 'text-sky-300' },
  stale: { label: 'Stopped', dot: 'bg-rose-400', text: 'text-rose-300' },
  paused: { label: 'Paused', dot: 'bg-amber-400', text: 'text-amber-300' },
  not_polling: { label: 'Polling off', dot: 'bg-amber-400', text: 'text-amber-300' },
  off: { label: 'Not set up', dot: 'bg-slate-600', text: 'text-slate-400' },
};
const ORDER: HealthState[] = ['stale', 'paused', 'not_polling', 'waiting', 'ok', 'off'];
const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';

function Fix({ row, onDone }: { row: IntegrationRow; onDone: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const f = row.fix;
  if (!f) return null;
  if (f.action === 'admin') {
    return <Link to="/admin" className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}>{f.label}</Link>;
  }
  const run = async () => {
    setBusy(true);
    setMsg(null);
    try {
      if (f.action === 'polling') await api.setPolling(row.key as 'deco' | 'hub' | 'adguard', true);
      else if (f.action === 'bufferbloat') await api.runBufferbloat();
      else if (f.action === 'exposure') await api.runExposure();
      else if (f.action === 'weekly') await api.generateWeeklyReport();
      setMsg('Started.');
      await onDone();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="inline-flex items-center gap-2">
      <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy} onClick={() => void run()}>{f.label}</button>
      {msg && <span className="text-xs text-slate-400">{msg}</span>}
    </span>
  );
}

/** System page: every feed NetWatch relies on, whether it is reading, and the fix. */
export function IntegrationHealthCard() {
  const { data, error, refresh } = usePolling(api.integrationHealth, 30_000);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;
  const rows = [...data.rows].sort((a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state));
  const now = Date.now();
  return (
    <ul className="divide-y divide-slate-800">
      {rows.map((r) => {
        const st = STATE[r.state] ?? STATE.off;
        return (
          <li key={r.key} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2">
            <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${st.dot}`} aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-sm font-medium text-slate-100">{r.label}</span>
                <span className={`text-xs ${st.text}`}>{st.label}</span>
                {r.last_ok_ms && <span className="text-xs text-slate-500">last good read {fmtAgo(r.last_ok_ms, now)}</span>}
                {r.reads_24h && r.reads_24h.total > 0 && (
                  <span className="text-xs text-slate-500">{r.reads_24h.ok} of {r.reads_24h.total} reads ok in 24 h</span>
                )}
              </div>
              <p className="text-xs text-slate-400">{r.detail}</p>
              {r.error && <p className="break-words text-xs text-rose-300">{r.error}</p>}
            </div>
            <Fix row={r} onDone={refresh} />
          </li>
        );
      })}
    </ul>
  );
}

const listNames = (n: string[]) => (n.length > 1 ? `${n.slice(0, -1).join(', ')} and ${n[n.length - 1]}` : n[0] ?? '');

/** Overview: a single line, only when a feed has stopped or is paused. */
export function IntegrationProblems() {
  const { data } = usePolling(api.integrationHealth, 60_000);
  const bad = (Array.isArray(data?.rows) ? data!.rows : []).filter((r) => r.state === 'stale' || r.state === 'paused');
  if (!bad.length) return null;
  return (
    <p role="status" className="rounded-md bg-rose-950/40 p-2 text-sm text-rose-200 ring-1 ring-rose-900">
      {listNames(bad.map((r) => r.label))} {bad.length > 1 ? 'have' : 'has'} stopped working, so some pages are missing data.{' '}
      <Link to="/system" className="text-sky-300 hover:underline">See integration health</Link>
    </p>
  );
}
