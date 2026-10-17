import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { SmokeChart } from '../components/SmokeChart';
import { StatusBadge } from '../components/StatusBadge';
import { api, type ProbeResult } from '../lib/api';
import { fmtDuration, fmtMs, fmtPct } from '../lib/format';
import { findRange, RANGES, windowFor } from '../lib/ranges';
import { usePolling } from '../lib/usePolling';

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-slate-900 px-3 py-2 ring-1 ring-slate-800">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="font-mono text-sm text-slate-100">{value}</div>
    </div>
  );
}

export function TargetDetail() {
  const id = Number(useParams().id);
  const [params, setParams] = useSearchParams();
  const range = findRange(params.get('range'));
  const target = usePolling(() => api.target(id), 60_000, [id]);
  const status = usePolling(api.status, 5_000);
  const series = usePolling(
    () => {
      const w = windowFor(range);
      return api.series(id, w.start, w.end);
    },
    range.refreshMs,
    [id, range.key],
  );
  const [test, setTest] = useState<ProbeResult | string | null>(null);

  if (target.error) return <p className="text-rose-400">{target.error}</p>;
  const t = target.data;
  if (!t) return <p className="text-slate-500">Loading…</p>;
  const st = status.data?.targets.find((s) => s.id === id);
  const sum = series.data?.summary;
  const w = windowFor(range);

  async function runTest() {
    setTest('running');
    try {
      setTest(await api.testTarget(id));
    } catch (e) {
      setTest(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/" className="text-xs text-slate-500 hover:text-slate-300">← Overview</Link>
          <h1 className="mt-1 flex items-center gap-3 text-xl font-semibold text-white">
            {t.name} <StatusBadge state={t.enabled ? (st?.state ?? 'unknown') : 'unknown'} />
          </h1>
          <p className="text-sm text-slate-400">
            {t.kind.toUpperCase()} {t.kind === 'http' ? t.url : t.host}
            {t.port ? `:${t.port}` : ''} · every {t.interval_s}s · timeout {t.timeout_s}s
            {!t.enabled && ' · paused'}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={runTest} className="rounded-md bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700">
            Test now
          </button>
          <a href={api.exportUrl(id, w.start, w.end, 'csv')} className="rounded-md bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700">
            CSV
          </a>
          <a href={api.exportUrl(id, w.start, w.end, 'json')} className="rounded-md bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700">
            JSON
          </a>
        </div>
      </div>

      {test && (
        <p className="text-sm text-slate-300" role="status">
          {test === 'running'
            ? 'Probing…'
            : typeof test === 'string'
              ? `Test failed: ${test}`
              : test.ok
                ? `Reply in ${fmtMs(test.rtt_ms)}`
                : `No reply (${test.error ?? 'failed'})`}
        </p>
      )}

      <div className="flex flex-wrap gap-1" role="tablist" aria-label="Time range">
        {RANGES.map((r) => (
          <button
            key={r.key}
            role="tab"
            aria-selected={r.key === range.key}
            onClick={() => setParams({ range: r.key })}
            className={`rounded-md px-3 py-1 text-xs ${r.key === range.key ? 'bg-sky-600 text-white' : 'bg-slate-900 text-slate-400 hover:text-white'}`}
          >
            {r.label}
          </button>
        ))}
      </div>

      <div className="rounded-lg bg-slate-900/70 p-3 ring-1 ring-slate-800">
        {series.error ? (
          <p className="p-6 text-rose-400">{series.error}</p>
        ) : series.data ? (
          series.data.summary.count ? (
            <SmokeChart data={series.data} />
          ) : (
            <p className="p-6 text-slate-500">No samples in this range yet.</p>
          )
        ) : (
          <p className="p-6 text-slate-500">Loading…</p>
        )}
        {series.data && (
          <p className="px-1 pt-2 text-[11px] text-slate-500">
            Bucket {fmtDuration(series.data.step)} · source {series.data.source === 'raw' ? 'raw samples' : '1-minute rollups'} ·
            shading = p5–p95 / p25–p75 spread, line = median, red bars = loss
          </p>
        )}
      </div>

      {sum && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
          <Stat label="Probes" value={String(sum.count)} />
          <Stat label="Loss" value={fmtPct(sum.loss_pct)} />
          <Stat label="Min" value={fmtMs(sum.min)} />
          <Stat label="Median" value={fmtMs(sum.median)} />
          <Stat label="Avg" value={fmtMs(sum.avg)} />
          <Stat label="p95" value={fmtMs(sum.p95)} />
          <Stat label="Max" value={fmtMs(sum.max)} />
          <Stat label="Jitter" value={fmtMs(sum.jitter)} />
        </div>
      )}
    </div>
  );
}
