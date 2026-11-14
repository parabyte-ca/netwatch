import { useState } from 'react';
import { api, type DomainCount } from '../lib/api';
import { usePolling } from '../lib/usePolling';

const SPANS = [
  { label: '24 h', hours: 24 },
  { label: '7 days', hours: 168 },
];

function Domains({ title, items, tone }: { title: string; items: DomainCount[]; tone: string }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-1 text-xs font-medium text-slate-400">{title}</h3>
      {items.length ? (
        <ul className="space-y-0.5 text-sm">
          {items.map((d) => (
            <li key={d.domain} className="flex items-baseline justify-between gap-2">
              <span className={`min-w-0 truncate font-mono text-xs ${tone}`} title={d.domain}>{d.domain}</span>
              <span className="shrink-0 tabular-nums text-slate-400">{d.queries.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-slate-500">None in this period.</p>
      )}
    </div>
  );
}

/** DNS queries, block rate and top domains for one device, from AdGuard Home. */
export function DeviceDns({ id }: { id: number }) {
  const [hours, setHours] = useState(24);
  const { data, error } = usePolling(() => api.deviceDns(id, hours), 60_000, [id, hours]);
  if (!data && !error) return null;
  if (data && !data.configured && data.queries === 0) return null;
  const max = Math.max(1, ...(data?.hourly ?? []).map((h) => h.queries));
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="DNS">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">DNS</h2>
        <div className="inline-flex overflow-hidden rounded ring-1 ring-slate-700" role="group" aria-label="DNS range">
          {SPANS.map((s) => (
            <button key={s.hours} onClick={() => setHours(s.hours)} aria-pressed={s.hours === hours}
              className={`px-2 py-0.5 text-xs ${s.hours === hours ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}>
              {s.label}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="text-sm text-rose-400">{error}</p>}
      {data && (
        <div className="space-y-3">
          <p className="text-sm text-slate-300">
            {data.queries.toLocaleString()} queries
            {data.blocked_pct != null && <>, {data.blocked_pct}% blocked</>}
            {data.failed_pct != null && data.failed_pct > 0 && <>, {data.failed_pct}% failed</>}
          </p>
          {data.hourly.length > 0 && (
            <div className="flex h-16 items-end gap-px" aria-label="Queries per hour">
              {data.hourly.map((h) => (
                <div key={h.hour_ms} className="relative min-w-[2px] flex-1 bg-sky-500/60"
                  style={{ height: `${Math.max(2, (100 * h.queries) / max)}%` }}
                  title={`${new Date(h.hour_ms).toLocaleString('en-CA', { weekday: 'short', hour: 'numeric' })}: ${h.queries} queries, ${h.blocked} blocked`}>
                  <div className="absolute bottom-0 w-full bg-violet-400" style={{ height: `${h.queries ? (100 * h.blocked) / h.queries : 0}%` }} />
                </div>
              ))}
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Domains title="Top domains" items={data.top_domains} tone="text-slate-200" />
            <Domains title="Top blocked domains" items={data.top_blocked} tone="text-violet-300" />
          </div>
          <p className="text-xs text-slate-500">From AdGuard Home. Domain names are kept as long as raw samples (7 days by default).</p>
        </div>
      )}
    </section>
  );
}
