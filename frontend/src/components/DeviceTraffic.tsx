import { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { api, type DeviceTraffic as Data } from '../lib/api';
import { fmtBps, fmtBytes } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const SPANS = [
  { label: '1 h', hours: 1 },
  { label: '24 h', hours: 24 },
  { label: '7 days', hours: 168 },
  { label: '30 days', hours: 720 },
];

function Chart({ data }: { data: Data }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const s = data.series;
    const end = Math.floor(Date.now() / 1000);
    const few = s.length < 3;
    const axis = { stroke: '#94a3b8', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' } };
    const plot = new uPlot(
      {
        width: el.clientWidth || 600,
        height: 200,
        // Always the full requested period, also with only a few points recorded so far.
        scales: { x: { time: true, range: () => [end - data.hours * 3600, end] }, y: { range: (_u, _min, max) => [0, Math.max(1e5, (max ?? 0) * 1.1)] } },
        axes: [axis, { ...axis, size: 70, values: (_u, vals) => vals.map((v) => fmtBps(v)) }],
        series: [
          {},
          { label: 'download', stroke: '#38bdf8', fill: 'rgba(56,189,248,0.15)', width: 1.5, points: { show: few, size: 6 }, value: (_u, v) => fmtBps(v) },
          { label: 'upload', stroke: '#a78bfa', width: 1.5, points: { show: few, size: 6 }, value: (_u, v) => fmtBps(v) },
        ],
      },
      [s.map((p) => p.ts), s.map((p) => p.down_bps), s.map((p) => p.up_bps)],
      el,
    );
    const ro = new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height: 200 }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      plot.destroy();
    };
  }, [data]);
  return <div ref={ref} className="w-full" data-testid="traffic-chart" />;
}

/** Bandwidth used by one device, from the Decos' per-client speeds. */
export function DeviceTraffic({ id }: { id: number }) {
  const [hours, setHours] = useState(24);
  const { data, error } = usePolling(() => api.deviceTraffic(id, hours), 60_000, [id, hours]);
  if (data && data.series.length === 0 && hours === 24) {
    return <p className="text-sm text-slate-500">No traffic recorded (only Wi-Fi and wired clients the Decos see are measured).</p>;
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-slate-300">
          {data ? <>↓ {fmtBytes(data.down_bytes)} · ↑ {fmtBytes(data.up_bytes)}</> : '…'}
        </span>
        <div className="inline-flex overflow-hidden rounded ring-1 ring-slate-700" role="group" aria-label="Traffic range">
          {SPANS.map((s) => (
            <button key={s.hours} onClick={() => setHours(s.hours)} aria-pressed={s.hours === hours}
              className={`px-2 py-0.5 text-xs ${s.hours === hours ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}>
              {s.label}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="text-sm text-rose-400">{error}</p>}
      {data && <Chart data={data} />}
    </div>
  );
}
