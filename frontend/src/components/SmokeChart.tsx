import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import type { SeriesResponse } from '../lib/api';
import { fmtAxisMs, fmtMs, fmtPct } from '../lib/format';

/**
 * SmokePing-style latency graph: the shaded "smoke" is the p5–p95 and p25–p75 spread of RTTs in
 * each bucket, the line is the median, and red bars along the bottom show packet loss.
 */
export function SmokeChart({ data, height = 320 }: { data: SeriesResponse; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const s = data.series;
    const lossBars = s.loss_pct.map((v) => (v ? v : null));
    const aligned: uPlot.AlignedData = [s.ts, s.p95, s.p75, s.median, s.p25, s.p5, lossBars];
    const axisStyle = { stroke: '#94a3b8', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' } };

    const opts: uPlot.Options = {
      width: el.clientWidth || 800,
      height,
      cursor: { drag: { x: true, y: false } },
      scales: {
        x: { time: true, range: () => [data.start, data.end] },
        ms: { auto: true, range: (_u, _min, max) => [0, Math.max(1, (max ?? 1) * 1.1)] },
        loss: { range: [0, 100] },
      },
      axes: [
        { ...axisStyle },
        { ...axisStyle, scale: 'ms', size: 64, values: (_u, vals) => vals.map(fmtAxisMs) },
        {
          ...axisStyle,
          scale: 'loss',
          side: 1,
          grid: { show: false },
          size: 48,
          values: (_u, vals) => vals.map((v) => `${v}%`),
        },
      ],
      series: [
        {},
        { label: 'p95', scale: 'ms', stroke: 'transparent', points: { show: false }, value: (_u, v) => fmtMs(v) },
        { label: 'p75', scale: 'ms', stroke: 'transparent', points: { show: false }, value: (_u, v) => fmtMs(v) },
        { label: 'median', scale: 'ms', stroke: '#38bdf8', width: 1.5, points: { show: false }, value: (_u, v) => fmtMs(v) },
        { label: 'p25', scale: 'ms', stroke: 'transparent', points: { show: false }, value: (_u, v) => fmtMs(v) },
        { label: 'p5', scale: 'ms', stroke: 'transparent', points: { show: false }, value: (_u, v) => fmtMs(v) },
        {
          label: 'loss',
          scale: 'loss',
          stroke: '#ef4444',
          fill: 'rgba(239,68,68,0.55)',
          paths: uPlot.paths.bars!({ size: [1, 100], gap: 0 }),
          points: { show: false },
          value: (_u, v) => fmtPct(v ?? 0),
        },
      ],
      bands: [
        { series: [1, 5], fill: 'rgba(148,163,184,0.18)' },
        { series: [2, 4], fill: 'rgba(148,163,184,0.32)' },
      ],
      legend: { live: true },
    };

    plot.current?.destroy();
    plot.current = new uPlot(opts, aligned, el);

    const ro = new ResizeObserver(() => plot.current?.setSize({ width: el.clientWidth, height }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      plot.current?.destroy();
      plot.current = null;
    };
  }, [data, height]);

  return <div ref={ref} className="w-full" data-testid="smoke-chart" />;
}
