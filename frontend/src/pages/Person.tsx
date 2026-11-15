import { useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, type PersonHistory } from '../lib/api';
import { colourOf } from '../lib/groups';
import { usePolling } from '../lib/usePolling';
import { clock, presenceText } from '../components/People';

const DAY_MS = 86_400_000;

function DayBar({ day, colour }: { day: PersonHistory['days'][number]; colour: string }) {
  const c = colourOf(colour);
  const label = new Date(day.start_ms).toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
  const home = day.spans.filter((s) => s.home).reduce((n, s) => n + s.end_ms - s.start_ms, 0);
  return (
    <li className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-2">
      <span className="text-xs text-slate-400">{label}</span>
      <div className="relative h-4 overflow-hidden rounded bg-slate-950 ring-1 ring-slate-800"
        role="img" aria-label={`${label}: home for ${Math.round(home / 3_600_000)} h`}>
        {day.spans.map((s) => (
          <span key={s.start_ms} title={`${clock(s.start_ms)} to ${clock(s.end_ms)}: ${s.home === null ? 'unknown' : s.home ? 'home' : 'away'}`}
            className={`absolute inset-y-0 ${s.home ? c.dot : s.home === false ? 'bg-slate-800' : 'bg-transparent'}`}
            style={{ left: `${((s.start_ms - day.start_ms) / DAY_MS) * 100}%`, width: `${((s.end_ms - s.start_ms) / DAY_MS) * 100}%` }} />
        ))}
      </div>
    </li>
  );
}

/** One person: home or away now, today's arrivals and departures, and the last 7 days. */
export function Person() {
  const id = Number(useParams().id);
  const fetcher = useCallback(() => api.personHistory(id, 7), [id]);
  const { data, error } = usePolling(fetcher, 60_000);
  if (error) return <p className="text-rose-400">{error}</p>;
  if (!data) return <p className="text-slate-400">Loading…</p>;
  const p = data.person;
  const c = colourOf(p.colour);
  const today = data.days[data.days.length - 1];
  const todays = data.events.filter((e) => today && e.ts_ms >= today.start_ms).reverse();
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`h-4 w-4 rounded-full ${c.dot}`} aria-hidden />
        <h1 className="text-2xl font-semibold text-white">{p.name}</h1>
        <span className={`rounded-full px-3 py-1 text-sm ring-1 ${p.home ? c.chip : 'bg-slate-900 text-slate-400 ring-slate-700'}`}>{presenceText(p)}</span>
        <Link to="/timeline" className="ml-auto text-sm text-sky-400 hover:underline">Timeline</Link>
      </div>

      <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Today</h2>
        {todays.length ? (
          <ul className="mt-2 space-y-1 text-sm text-slate-200">
            {todays.map((e) => (
              <li key={e.ts_ms + e.action}>
                <span className="inline-block w-14 tabular-nums text-slate-400">{clock(e.ts_ms)}</span>
                {e.action === 'arrived' ? 'Arrived home' : 'Left'}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-slate-400">No arrivals or departures today.</p>
        )}
      </section>

      <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Last 7 days</h2>
        <ul className="mt-3 space-y-1.5">
          {data.days.map((d) => <DayBar key={d.date} day={d} colour={p.colour} />)}
        </ul>
        <div className="mt-1 grid grid-cols-[5.5rem_minmax(0,1fr)] gap-2 text-[10px] text-slate-500">
          <span />
          <span className="flex justify-between"><span>0:00</span><span>6:00</span><span>12:00</span><span>18:00</span><span>24:00</span></span>
        </div>
        <p className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-400">
          <span className="inline-flex items-center gap-1"><span className={`h-2 w-3 rounded-sm ${c.dot}`} /> Home</span>
          <span className="inline-flex items-center gap-1"><span className="h-2 w-3 rounded-sm bg-slate-800" /> Away</span>
          <span>Blank: before NetWatch knew.</span>
        </p>
      </section>

      <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Devices</h2>
        {data.devices.length ? (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {data.devices.map((d) => <li key={d.id}><Link to={`/devices/${d.id}`} className="text-sky-400 hover:underline">{d.label}</Link></li>)}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-slate-400">No devices yet. Add them under Devices → People…</p>
        )}
        {p.last_seen_ms && <p className="mt-2 text-xs text-slate-500">Last seen {clock(p.last_seen_ms)}.</p>}
      </section>
    </div>
  );
}
