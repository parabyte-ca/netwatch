import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { api, type SpeedPlanView, type SpeedPoint } from '../lib/api';
import { usePolling } from '../lib/usePolling';

const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v)} %`);
const mbps = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v)} Mb/s`);

/** "% of plan" as a short phrase for one test, e.g. "1,380 Mb/s (92 % of plan)". */
export function ofPlan(mb: number | null, p: number | null): string {
  if (mb == null) return '—';
  return `${Math.round(mb).toLocaleString('en-CA')} Mb/s${p != null ? ` (${Math.round(p)} % of plan)` : ''}`;
}

function Chart({ view }: { view: SpeedPlanView }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const pts = view.points;
    const end = Math.floor(Date.now() / 1000);
    const thr = view.plan.plan_slow_pct;
    const axis = { stroke: '#94a3b8', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' } };
    const flat = (v: number) => pts.map(() => v);
    const plot = new uPlot(
      {
        width: el.clientWidth || 600,
        height: 200,
        scales: { x: { time: true, range: () => [end - 90 * 86_400, end] }, y: { range: (_u, _min, max) => [0, Math.max(110, (max ?? 0) * 1.05)] } },
        axes: [axis, { ...axis, size: 48, values: (_u, vals) => vals.map((v) => `${v} %`) }],
        series: [
          {},
          { label: 'download', stroke: '#38bdf8', width: 1.5, points: { show: true, size: 5 }, value: (_u, v) => pct(v) },
          { label: 'upload', stroke: '#a78bfa', width: 1.5, points: { show: true, size: 5 }, value: (_u, v) => pct(v) },
          { label: 'plan', stroke: '#475569', width: 1, dash: [4, 4], points: { show: false } },
          { label: `slow (${thr} %)`, stroke: '#f59e0b', width: 1, dash: [2, 4], points: { show: false } },
        ],
      },
      [pts.map((p) => p.ts_ms / 1000), pts.map((p) => p.down_pct), pts.map((p) => p.up_pct), flat(100), flat(thr)],
      el,
    );
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height: 200 }));
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      plot.destroy();
    };
  }, [view]);
  return <div ref={ref} className="w-full" data-testid="speed-plan-chart" />;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-md bg-slate-950 p-2 ring-1 ring-slate-800">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-base tabular-nums text-slate-100">{value}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

function linkNote(last: SpeedPoint | null) {
  if (!last?.link_mbps) return null;
  return last.link_capped
    ? `NetWatch's network port runs at ${last.link_mbps.toLocaleString('en-CA')} Mb/s and was the limit, so the line may be faster than measured.`
    : `NetWatch's network port: ${last.link_mbps.toLocaleString('en-CA')} Mb/s.`;
}

/** Health page: the nightly load test as a share of the plan, over 90 days. */
export function SpeedPlanCard() {
  const { data, error } = usePolling(() => api.speedPlan(90), 300_000);
  if (error) return <p className="text-sm text-rose-400">{error}</p>;
  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;
  if (!data.set) {
    return (
      <p className="text-sm text-slate-400">
        Enter your plan's download and upload speeds under <Link to="/admin" className="text-sky-400 hover:underline">Admin → Internet plan</Link> to see
        each night's test as a share of what you pay for.
      </p>
    );
  }
  const m = data.median_30d;
  const last = data.last;
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-400">
        Plan: {mbps(data.plan.plan_down_mbps || null)} down, {mbps(data.plan.plan_up_mbps || null)} up. From the nightly load test (<Link to="/diagnostics" className="text-sky-400 hover:underline">run one now</Link>).
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Last ↓" value={pct(last?.down_pct)} sub={mbps(last?.down_mbps)} />
        <Stat label="Last ↑" value={pct(last?.up_pct)} sub={mbps(last?.up_mbps)} />
        <Stat label="30-day median ↓" value={pct(m.down_pct)} sub={mbps(m.down_mbps)} />
        <Stat label="30-day median ↑" value={pct(m.up_pct)} sub={`${mbps(m.up_mbps)} · ${m.tests} test${m.tests === 1 ? '' : 's'}`} />
      </div>
      {data.open_event_id != null && (
        <p className="rounded-md bg-amber-950/60 px-3 py-2 text-sm text-amber-200">
          Slower than your plan for several nights. <Link to={`/events/${data.open_event_id}`} className="underline">Details</Link>
        </p>
      )}
      {data.points.length ? <Chart view={data} /> : <p className="text-sm text-slate-500">No test yet.</p>}
      {linkNote(last) && <p className="text-xs text-slate-400">{linkNote(last)}</p>}
    </div>
  );
}
