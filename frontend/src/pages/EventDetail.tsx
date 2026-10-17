import { Link, useParams } from 'react-router-dom';
import { EventRow } from '../components/EventRow';
import { HopTable } from '../components/HopTable';
import { SmokeChart } from '../components/SmokeChart';
import { api } from '../lib/api';
import { EVENT_LABEL, fmtDurationMs, fmtTime, SEVERITY_DOT } from '../lib/format';
import { usePolling } from '../lib/usePolling';

export function EventDetail() {
  const id = Number(useParams().id);
  const { data, error } = usePolling(() => api.event(id), 15_000, [id]);
  const targetId = data?.target?.id;
  const win = data?.window;
  const series = usePolling(
    () => (targetId && win ? api.series(targetId, Math.floor(win.start_ms / 1000), Math.ceil(win.end_ms / 1000), 600) : Promise.resolve(null)),
    30_000,
    [targetId, win?.start_ms, win?.end_ms],
  );

  if (error) return <p className="text-rose-400">{error}</p>;
  if (!data) return <p className="text-slate-500">Loading…</p>;
  const ev = data.event;
  const details = Object.entries(ev.details).filter(([, v]) => v !== null && v !== '' && typeof v !== 'object');

  return (
    <div className="space-y-5">
      <div>
        <Link to="/timeline" className="text-xs text-slate-500 hover:text-slate-300">← Timeline</Link>
        <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold text-white">
          <span className={`h-2.5 w-2.5 rounded-full ${SEVERITY_DOT[ev.severity]}`} />
          {ev.title}
        </h1>
        <p className="text-sm text-slate-400">
          {EVENT_LABEL[ev.kind]} · started {fmtTime(ev.start_ms)}
          {ev.end_ms !== null && ev.end_ms !== ev.start_ms && <> · ended {fmtTime(ev.end_ms)}</>}
          {' · '}
          {ev.open ? <span className="text-rose-300">ongoing</span> : ev.end_ms === ev.start_ms ? 'instant' : fmtDurationMs(ev.duration_ms)}
          {data.target && <> · <Link to={`/targets/${data.target.id}`} className="text-sky-400 hover:underline">{data.target.name}</Link></>}
        </p>
        {details.length > 0 && (
          <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
            {details.map(([k, v]) => (
              <div key={k}><dt className="inline text-slate-500">{k.replace(/_/g, ' ')}: </dt><dd className="inline text-slate-300">{String(v)}</dd></div>
            ))}
          </dl>
        )}
      </div>

      {series.data && series.data.summary.count > 0 && (
        <section className="rounded-lg bg-slate-900/70 p-3 ring-1 ring-slate-800" aria-label="Latency around the event">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Latency around the event</h2>
          <SmokeChart data={series.data} height={240} />
        </section>
      )}

      <section className="rounded-lg bg-slate-900 p-3 ring-1 ring-slate-800" aria-label="Failing at the same time">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Failing at the same time (±5 min)</h2>
        {data.concurrent_failures.length ? data.concurrent_failures.map((e) => <EventRow key={e.id} ev={e} compact />) : (
          <p className="text-sm text-slate-500">Nothing else failed: the problem is specific to this target or its path.</p>
        )}
      </section>

      <section className="rounded-lg bg-slate-900 p-3 ring-1 ring-slate-800" aria-label="Host NIC events">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">TrueNAS NIC (±5 min)</h2>
        {data.nic_events.length ? data.nic_events.map((e) => <EventRow key={e.id} ev={e} compact />) : (
          <p className="text-sm text-slate-500">No link drops, speed changes or errors on the TrueNAS NIC.</p>
        )}
      </section>

      <section className="rounded-lg bg-slate-900 p-3 ring-1 ring-slate-800" aria-label="Path tests">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Path tests</h2>
        {data.path_tests.length ? (
          <div className="space-y-4">{data.path_tests.map((t) => <div key={t.id}><p className="mb-1 text-xs text-slate-400">to {t.host}</p><HopTable test={t} /></div>)}</div>
        ) : (
          <p className="text-sm text-slate-500">No path tests in this window.</p>
        )}
      </section>
    </div>
  );
}
