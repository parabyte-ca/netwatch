import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { fmtBps, fmtBytes } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const SPANS = [
  { key: 'now', label: 'Now', hours: 0 },
  { key: '24h', label: '24 h', hours: 24 },
  { key: '7d', label: '7 days', hours: 24 * 7 },
] as const;

type Row = { key: string; id: number | null; label: string; down: number; up: number; sub?: string };

function Bars({ rows, fmt }: { rows: Row[]; fmt: (v: number) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.down + r.up));
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.key} className="text-sm">
          <div className="flex items-baseline justify-between gap-2">
            {r.id != null ? (
              <Link to={`/devices/${r.id}`} className="min-w-0 truncate text-slate-100 hover:text-sky-300">{r.label}</Link>
            ) : (
              <span className="min-w-0 truncate font-mono text-xs text-slate-300">{r.label}</span>
            )}
            <span className="shrink-0 text-xs tabular-nums text-slate-400">↓ {fmt(r.down)} · ↑ {fmt(r.up)}</span>
          </div>
          <div className="mt-1 flex h-1.5 overflow-hidden rounded bg-slate-800" aria-hidden>
            <div className="bg-sky-500" style={{ width: `${(100 * r.down) / max}%` }} />
            <div className="bg-violet-500" style={{ width: `${(100 * r.up) / max}%` }} />
          </div>
          {r.sub && <div className="text-[11px] text-slate-500">{r.sub}</div>}
        </li>
      ))}
    </ul>
  );
}

/** Who is using the bandwidth: live rates from the Decos, or totals over a period. */
export function TopTalkers({ limit = 8, compact = false }: { limit?: number; compact?: boolean }) {
  const [span, setSpan] = useState<(typeof SPANS)[number]>(SPANS[0]);
  const live = usePolling(api.trafficLive, 10_000);
  const top = usePolling(() => (span.hours ? api.trafficTop(span.hours) : Promise.resolve(null)), 60_000, [span.key]);
  if (live.data && !live.data.deco_api) {
    return compact ? null : (
      <section aria-label="Top talkers" className="rounded-lg bg-slate-900 p-3 text-sm text-slate-400 ring-1 ring-slate-800">
        Per-device bandwidth comes from the Decos: connect them under{' '}
        <Link to="/admin" className="text-sky-400 hover:underline">Admin → Deco mesh</Link>.
      </section>
    );
  }
  const rows: Row[] =
    span.hours === 0
      ? (live.data?.devices ?? []).slice(0, limit).map((d) => ({
          key: d.device_id != null ? `d${d.device_id}` : d.macs[0]!, id: d.device_id, label: d.label,
          down: d.down_bps, up: d.up_bps, sub: compact ? undefined : [d.deco_name && `Deco ${d.deco_name}`, d.connection].filter(Boolean).join(' · '),
        }))
      : (top.data?.devices ?? []).slice(0, limit).map((d) => ({
          key: d.device_id != null ? `d${d.device_id}` : d.macs[0]!, id: d.device_id, label: d.label, down: d.down_bytes, up: d.up_bytes,
        }));
  const fmt = span.hours === 0 ? fmtBps : fmtBytes;
  const total = span.hours === 0
    ? live.data && `↓ ${fmtBps(live.data.down_bps)} · ↑ ${fmtBps(live.data.up_bps)}`
    : top.data && `↓ ${fmtBytes(top.data.down_bytes)} · ↑ ${fmtBytes(top.data.up_bytes)}`;
  return (
    <section aria-label="Top talkers" className={compact ? 'space-y-2' : 'rounded-lg bg-slate-900 p-3 ring-1 ring-slate-800'}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Top talkers</h2>
        <div className="inline-flex overflow-hidden rounded ring-1 ring-slate-700" role="group" aria-label="Traffic period">
          {SPANS.map((s) => (
            <button key={s.key} onClick={() => setSpan(s)} aria-pressed={s.key === span.key}
              className={`px-2 py-0.5 text-xs ${s.key === span.key ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}>
              {s.label}
            </button>
          ))}
        </div>
      </div>
      {total && <p className="text-xs text-slate-500">All devices: {total}</p>}
      {rows.length ? <Bars rows={rows} fmt={fmt} /> : <p className="text-sm text-slate-500">No traffic recorded yet.</p>}
    </section>
  );
}
