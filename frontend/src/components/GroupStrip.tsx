import { useState } from 'react';
import { api, type GroupStats } from '../lib/api';
import { fmtBytes } from '../lib/format';
import { GroupIcon, colourOf } from '../lib/groups';
import { usePolling } from '../lib/usePolling';

/** "Groups at a glance": one card per group with devices online, traffic, DNS and alerts. */
export function GroupStrip({ selected, onSelect }: { selected: string; onSelect: (groupId: string) => void }) {
  const [hours, setHours] = useState<24 | 168>(24);
  const { data } = usePolling(() => api.groupStats(hours), 60_000, [hours]);
  if (!data || !Array.isArray(data) || data.length === 0 || data.every((g) => g.id === 0)) return null;
  return (
    <section className="space-y-2" aria-label="Groups at a glance">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Groups at a glance</h2>
        <div className="inline-flex overflow-hidden rounded ring-1 ring-slate-700" role="group" aria-label="Group totals range">
          {([24, 168] as const).map((h) => (
            <button key={h} onClick={() => setHours(h)} aria-pressed={h === hours}
              className={`px-2 py-0.5 text-xs ${h === hours ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}>
              {h === 24 ? '24 h' : '7 days'}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {data.map((g: GroupStats) => {
          const c = colourOf(g.colour);
          const key = String(g.id);
          const on = selected === key;
          return (
            <button key={g.id} onClick={() => onSelect(on ? '' : key)} aria-pressed={on}
              className={`space-y-1 rounded-lg bg-slate-900 p-3 text-left ring-1 transition hover:ring-slate-600 ${on ? 'ring-2 ring-sky-500' : 'ring-slate-800'}`}
              style={{ borderLeft: `4px solid ${c.hex}` }}>
              <div className="flex items-center gap-2">
                <span style={{ color: c.hex }}><GroupIcon icon={g.icon} className="h-4 w-4" /></span>
                <span className="flex-1 truncate text-sm font-medium text-slate-100">{g.name}</span>
                {g.quiet && <span className="rounded bg-slate-800 px-1.5 text-[10px] text-slate-400">quiet</span>}
              </div>
              <p className="text-xs text-slate-400">
                <span className="text-slate-200">{g.online}</span>/{g.devices} online · ↓{fmtBytes(g.down_bytes)} ↑{fmtBytes(g.up_bytes)}
              </p>
              <p className="text-xs text-slate-400">
                {g.dns ? <>{g.dns.queries.toLocaleString()} DNS queries{g.dns.blocked_pct != null && <>, {g.dns.blocked_pct}% blocked</>}</> : 'No DNS data'}
              </p>
              <p className="text-xs">
                {g.outages > 0 && <span className="mr-2 text-rose-300">{g.outages} outage{g.outages === 1 ? '' : 's'}</span>}
                {g.alerts > 0 && <span className="mr-2 text-amber-300">{g.alerts} alert{g.alerts === 1 ? '' : 's'}</span>}
                {g.quiet_events > 0 && <span className="text-slate-500">{g.quiet_events} quiet</span>}
                {g.outages + g.alerts + g.quiet_events === 0 && <span className="text-emerald-300">No alerts</span>}
              </p>
              {g.top_talker && <p className="truncate text-[11px] text-slate-500">Busiest: {g.top_talker}</p>}
            </button>
          );
        })}
      </div>
    </section>
  );
}
