import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { api, type AdGuardSummary } from '../lib/api';
import { fmtAgo } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const SPANS = [
  { label: '6 h', hours: 6 },
  { label: '24 h', hours: 24 },
  { label: '7 days', hours: 168 },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label={title}>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
      {children}
    </section>
  );
}

function Stat({ label, value, tone = 'text-slate-100' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md bg-slate-950 px-3 py-2 ring-1 ring-slate-800">
      <p className="text-xs text-slate-400">{label}</p>
      <p className={`text-lg font-semibold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}

const ms = (v: number | null | undefined) => (v == null ? '–' : `${Math.round(v)} ms`);
const pct = (v: number | null | undefined) => (v == null ? '–' : `${v}%`);

type Line = { label: string; stroke: string; fill?: string; values: (number | null)[]; fmt: (v: number | null | undefined) => string };

/** One uPlot time chart over the selected period. */
function TimeChart({ data, lines, height, yMax, unit, testId }: {
  data: AdGuardSummary; lines: Line[]; height: number; yMax: (max: number) => number; unit: (v: number) => string; testId: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const end = Math.floor(Date.now() / 1000);
    const few = data.series.length < 3;
    const axis = { stroke: '#94a3b8', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' } };
    const plot = new uPlot(
      {
        width: el.clientWidth || 600,
        height,
        scales: { x: { time: true, range: () => [end - data.hours * 3600, end] }, y: { range: (_u, _min, max) => [0, yMax(max ?? 0)] } },
        axes: [axis, { ...axis, size: 60, values: (_u, vals) => vals.map(unit) }],
        series: [{}, ...lines.map((l) => ({ label: l.label, stroke: l.stroke, fill: l.fill, width: 1.5, points: { show: few, size: 6 }, value: (_u: uPlot, v: number | null) => l.fmt(v) }))],
      },
      [data.series.map((p) => p.ts_ms / 1000), ...lines.map((l) => l.values)],
      el,
    );
    const ro = new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      plot.destroy();
    };
  }, [data, lines, height, yMax, unit]);
  return <div ref={ref} className="w-full" data-testid={testId} />;
}

const latencyMax = (max: number) => Math.max(50, max * 1.1);
const rateMax = (max: number) => Math.min(100, Math.max(25, max * 1.2));
const msUnit = (v: number) => `${v} ms`;
const pctUnit = (v: number) => `${v}%`;

/** Response time (average and 95th percentile), then block rate, per minute. */
function Charts({ data }: { data: AdGuardSummary }) {
  const [latency, rate] = useMemo<[Line[], Line[]]>(() => {
    const s = data.series;
    return [
      [
        { label: 'average', stroke: '#38bdf8', fill: 'rgba(56,189,248,0.12)', values: s.map((p) => p.avg_ms), fmt: ms },
        { label: '95th percentile', stroke: '#f59e0b', values: s.map((p) => p.p95_ms), fmt: ms },
      ],
      [
        {
          label: 'blocked', stroke: '#a78bfa', fill: 'rgba(167,139,250,0.12)',
          values: s.map((p) => (p.reachable && p.queries ? Math.round((1000 * (p.blocked ?? 0)) / p.queries) / 10 : null)),
          fmt: (v: number | null | undefined) => (v == null ? '–' : `${v}%`),
        },
      ],
    ];
  }, [data]);
  return (
    <div className="space-y-2">
      <TimeChart data={data} lines={latency} height={200} yMax={latencyMax} unit={msUnit} testId="dns-chart" />
      <TimeChart data={data} lines={rate} height={110} yMax={rateMax} unit={pctUnit} testId="block-chart" />
    </div>
  );
}

/** The LAN's DNS server (AdGuard Home): response time, block rate, upstream health and busiest clients. */
export function Dns() {
  const [hours, setHours] = useState(24);
  const { data, error } = usePolling(() => api.adguardSummary(hours), 60_000, [hours]);
  const configured = !!data && !!data.host && data.password_set;
  const down = data?.latest && !data.latest.reachable;
  // The last minute with answers (an idle minute has no response time).
  const recent = data && !down ? [...data.series].reverse().find((p) => p.avg_ms != null)?.avg_ms : null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">DNS</h1>
          <p className="text-xs text-slate-400">
            {!data ? 'Loading…' : !configured ? 'AdGuard Home is not connected.' : (
              <>
                AdGuard Home{data.status?.version ? ` ${data.status.version}` : ''} at {data.host}
                {data.last_ok_ms ? `, last read ${fmtAgo(data.last_ok_ms, Date.now())}` : ', not read yet'}
                {!data.enabled && ' (polling is off)'}.
              </>
            )}
          </p>
        </div>
        {configured && (
          <div className="inline-flex overflow-hidden rounded ring-1 ring-slate-700" role="group" aria-label="DNS range">
            {SPANS.map((s) => (
              <button key={s.hours} onClick={() => setHours(s.hours)} aria-pressed={s.hours === hours}
                className={`px-2 py-0.5 text-xs ${s.hours === hours ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}>
                {s.label}
              </button>
            ))}
          </div>
        )}
      </div>
      {error && <p className="text-sm text-rose-400">{error}</p>}

      {data && !configured && (
        <Section title="Connect AdGuard Home">
          <p className="text-sm text-slate-400">
            Enter your AdGuard Home address and a NetWatch user under{' '}
            <Link to="/admin" className="text-sky-400 hover:underline">Admin → AdGuard Home</Link>. The README explains how to set it
            up on a Raspberry Pi.
          </p>
        </Section>
      )}

      {data && configured && (
        <>
          {(down || data.error || data.paused || data.latest?.protection === false) && (
            <div className="space-y-1 rounded-lg bg-rose-950/40 p-3 text-sm ring-1 ring-rose-900">
              {down && <p className="text-rose-300">AdGuard Home is not answering: devices that use it cannot resolve names.</p>}
              {data.latest?.protection === false && <p className="text-amber-300">Protection is off: nothing is being blocked.</p>}
              {data.error && !down && <p className="text-rose-300">Last poll failed: {data.error}</p>}
              {data.paused && <p className="text-amber-300">Polling stopped ({data.paused}). Fix the password under Admin.</p>}
            </div>
          )}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Queries" value={data.queries.toLocaleString()} />
            <Stat label="Blocked" value={pct(data.blocked_pct)} />
            <Stat label="Failed" value={pct(data.failed_pct)} tone={(data.failed_pct ?? 0) >= 5 ? 'text-rose-300' : 'text-slate-100'} />
            <Stat label="Response time now" value={ms(recent)} />
          </div>

          <Section title="Response time and block rate">
            {data.series.length ? <Charts data={data} /> : <p className="text-sm text-slate-400">No readings in this period yet.</p>}
          </Section>

          <Section title="Upstream resolvers">
            {data.upstreams.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr><th className="py-1 pr-3">Upstream</th><th className="pr-3 text-right">Queries</th><th className="pr-3 text-right">Failed</th><th className="text-right">Average</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {data.upstreams.map((u) => (
                      <tr key={u.name}>
                        <td className="break-all py-1.5 pr-3 font-mono text-xs text-slate-200">{u.name}</td>
                        <td className="pr-3 text-right tabular-nums">{u.queries.toLocaleString()}</td>
                        <td className={`pr-3 text-right tabular-nums ${u.failed_pct >= 5 ? 'text-rose-300' : 'text-emerald-300'}`}>{u.failed_pct}%</td>
                        <td className="text-right tabular-nums">{ms(u.avg_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-slate-400">No upstream answers recorded (answers from AdGuard's cache are not counted, and the query log must be on).</p>
            )}
          </Section>

          <Section title="Busiest clients">
            {data.top_clients.length ? (
              <ul className="divide-y divide-slate-800">
                {data.top_clients.map((c) => (
                  <li key={c.client} className="flex items-baseline justify-between gap-2 py-1.5 text-sm">
                    <span className="min-w-0 truncate">
                      {c.device_id ? <Link to={`/devices/${c.device_id}`} className="text-slate-100 hover:text-sky-300">{c.label ?? c.client}</Link> : <span className="text-slate-300">Unknown device</span>}
                      <span className="ml-2 font-mono text-xs text-slate-500">{c.client}</span>
                    </span>
                    <span className="shrink-0 tabular-nums text-slate-300">{c.queries.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-400">No clients recorded yet.</p>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
