import { useState } from 'react';
import { Link } from 'react-router-dom';
import { CauseList } from '../components/CauseList';
import { api, type Incident, type TopologyNode } from '../lib/api';
import { fmtDurationMs, fmtTime, STATE_STYLE } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const SPANS = [
  { key: '24h', label: '24 hours', s: 86_400 },
  { key: '7d', label: '7 days', s: 604_800 },
  { key: '30d', label: '30 days', s: 2_592_000 },
];

function Tree({ nodes, highlight }: { nodes: TopologyNode[]; highlight: Set<string>; }) {
  const children = (parent: string | null) => nodes.filter((n) => n.parent === parent);
  const render = (n: TopologyNode, depth: number): React.ReactNode => {
    const style = STATE_STYLE[n.state] ?? STATE_STYLE.unknown;
    const hot = highlight.has(n.layer);
    return (
      <li key={n.layer}>
        <div
          className={`flex flex-wrap items-center gap-2 rounded-md px-2 py-1 ${hot ? 'bg-rose-950/60 ring-1 ring-rose-500/60' : ''}`}
          style={{ marginLeft: depth * 20 }}
          data-layer={n.layer}
        >
          <span className={`h-2.5 w-2.5 rounded-full ${style.dot}`} title={style.label} />
          <span className="text-sm text-slate-100">{n.label}</span>
          {n.inferred && <span className="text-[10px] uppercase tracking-wide text-slate-500">inferred</span>}
          <span className="text-xs text-slate-500">
            {n.members.length > 0 ? n.members.map((m) => m.name).join(', ') : n.inferred ? '' : 'no targets'}
          </span>
        </div>
        <ul>{children(n.layer).map((c) => render(c, depth + 1))}</ul>
      </li>
    );
  };
  return <ul className="space-y-0.5">{children(null).map((n) => render(n, 0))}</ul>;
}

function IncidentCard({ inc, selected, onSelect }: { inc: Incident; selected: boolean; onSelect: () => void }) {
  const top = inc.causes[0];
  return (
    <div className={`rounded-lg p-3 ring-1 ${selected ? 'bg-slate-900 ring-sky-600' : 'bg-slate-900/60 ring-slate-800'}`}>
      <button onClick={onSelect} className="flex w-full flex-wrap items-center gap-3 text-left" aria-expanded={selected}>
        <span className={`h-2 w-2 rounded-full ${inc.severity === 'critical' ? 'bg-rose-500' : 'bg-amber-400'} ${inc.open ? 'animate-pulse' : ''}`} />
        <span className="text-xs text-slate-400">{fmtTime(inc.start_ms)}</span>
        <span className="text-xs text-slate-500">{inc.open ? `ongoing · ${fmtDurationMs(inc.duration_ms)}` : fmtDurationMs(inc.duration_ms)}</span>
        <span className="min-w-0 flex-1 truncate text-sm text-slate-100">{top?.title}</span>
        {top && <span className="text-xs tabular-nums text-slate-400">{top.confidence} %</span>}
      </button>
      {selected && (
        <div className="mt-3 space-y-3 border-t border-slate-800 pt-3">
          <p className="text-xs text-slate-400">
            Failing: {inc.failing.map((f) => f.name).join(', ') || '—'}
            {inc.healthy.length > 0 && <> · Working: {inc.healthy.map((h) => h.name).join(', ')}</>}
          </p>
          <CauseList causes={inc.causes} />
          <p className="text-xs">
            {inc.event_ids.map((id, i) => (
              <Link key={id} to={`/events/${id}`} className="mr-3 text-sky-400 hover:underline">
                Event {i + 1}
              </Link>
            ))}
          </p>
        </div>
      )}
    </div>
  );
}

export function Diagnosis() {
  const [span, setSpan] = useState(SPANS[1]!);
  const [selected, setSelected] = useState<string | null>(null);
  const { data, error } = usePolling(
    () => api.diagnosis(Math.floor(Date.now() / 1000) - span.s),
    30_000,
    [span.key],
  );
  const current = data?.incidents.find((i) => i.id === selected) ?? null;
  const highlight = new Set<string>(current?.causes[0] ? [current.causes[0].layer] : []);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-white">Diagnosis</h1>
        <div className="flex items-center gap-3">
          <Link to="/report" className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500">
            Incident report for Bell
          </Link>
          <div className="flex gap-1" role="tablist" aria-label="Time span">
            {SPANS.map((s) => (
              <button key={s.key} role="tab" aria-selected={s.key === span.key} onClick={() => setSpan(s)}
                className={`rounded-md px-3 py-1 text-xs ${s.key === span.key ? 'bg-sky-600 text-white' : 'bg-slate-900 text-slate-400 hover:text-white'}`}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>
      {error && <p className="text-rose-400">{error}</p>}
      {data && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <div className="space-y-5">
            <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="Network map">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Network map</h2>
              <Tree nodes={data.topology} highlight={highlight} />
              <p className="mt-3 text-[11px] text-slate-500">
                Live state of each layer. Select an incident to highlight its most likely cause. "Inferred" layers
                can't be probed directly (an unmanaged switch, Bell's network) and are judged from the layers around them.
              </p>
            </section>
            <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="Patterns">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Patterns</h2>
              {data.patterns.length ? (
                <ul className="space-y-2 text-sm">
                  {data.patterns.map((p) => (
                    <li key={p.kind}>
                      <p className="text-slate-100">{p.title}</p>
                      <p className="text-xs text-slate-400">{p.hint}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-slate-500">No recurring pattern (needs at least 4 incidents).</p>
              )}
            </section>
          </div>
          <section aria-label="Incidents" className="space-y-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Incidents ({data.incidents.length})
            </h2>
            {data.incidents.length === 0 && (
              <p className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-400">
                No outages or packet loss in this period.
              </p>
            )}
            {data.incidents.map((inc) => (
              <IncidentCard key={inc.id} inc={inc} selected={inc.id === selected}
                onSelect={() => setSelected(inc.id === selected ? null : inc.id)} />
            ))}
          </section>
        </div>
      )}
    </div>
  );
}
