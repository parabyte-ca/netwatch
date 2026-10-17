import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api';
import { fmtBps, fmtMs } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const SPANS = [
  { key: '7d', label: 'Last 7 days', days: 7 },
  { key: '30d', label: 'Last 30 days', days: 30 },
  { key: '90d', label: 'Last 90 days', days: 90 },
];

const th = 'border-b border-slate-700 px-2 py-1 text-left font-medium print:border-slate-400';
const td = 'border-b border-slate-800 px-2 py-1 align-top print:border-slate-300';

export function Report() {
  const [span, setSpan] = useState(SPANS[1]!);
  const [scope, setScope] = useState<'all' | 'isp'>('isp');
  const start = Math.floor(Date.now() / 1000 / 3600) * 3600 - span.days * 86_400;
  const { data: r, error } = usePolling(() => api.report(start, scope), 300_000, [span.key, scope]);

  return (
    <div className="space-y-5 print:space-y-3 print:text-black">
      <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
        <Link to="/diagnosis" className="text-xs text-slate-500 hover:text-slate-300">← Diagnosis</Link>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Period" className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm"
            value={span.key} onChange={(e) => setSpan(SPANS.find((s) => s.key === e.target.value)!)}>
            {SPANS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <label className="inline-flex items-center gap-1.5 text-sm text-slate-300">
            <input type="checkbox" checked={scope === 'isp'} onChange={(e) => setScope(e.target.checked ? 'isp' : 'all')} />
            Only incidents pointing at Bell
          </label>
          <a href={api.reportMarkdownUrl(start, scope)} className="rounded-md bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-700">
            Download Markdown
          </a>
          <button onClick={() => window.print()} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500">
            Print / save as PDF
          </button>
        </div>
      </div>
      {error && <p className="text-rose-400">{error}</p>}
      {!r ? (
        <p className="text-slate-500">Building report…</p>
      ) : (
        <article className="space-y-5 text-sm text-slate-200 print:text-black">
          <header>
            <h1 className="text-xl font-semibold text-white print:text-black">Network incident report</h1>
            <ul className="mt-2 space-y-0.5 text-xs text-slate-400 print:text-black">
              <li>Period: {r.period.start} to {r.period.end}</li>
              <li>Generated {r.generated_at} by NetWatch {r.version}</li>
              <li>Connection: Bell fibre, Home Hub at {r.gateway ?? 'unknown'}; measured from a wired server on the home network</li>
              <li>Scope: {r.scope === 'isp' ? 'incidents attributed to the Bell network' : 'all incidents'}</li>
            </ul>
          </header>

          <section aria-label="Summary">
            <h2 className="mb-1 text-base font-semibold text-white print:text-black">Summary</h2>
            <ul className="list-disc space-y-0.5 pl-5">
              <li>Incidents: <b>{r.summary.incidents}</b> (attributed to Bell: <b>{r.summary.isp_incidents}</b>)</li>
              <li>Internet downtime attributed to Bell: <b>{r.summary.isp_downtime}</b> (availability {r.summary.isp_availability_pct} %)</li>
              <li>Longest incident: {r.summary.longest}</li>
              {r.patterns.map((p) => <li key={p.kind}>Pattern: {p.title}</li>)}
            </ul>
          </section>

          {r.ruled_out.length > 0 && (
            <section aria-label="Ruled out">
              <h2 className="mb-1 text-base font-semibold text-white print:text-black">Ruled out</h2>
              <ul className="list-disc space-y-0.5 pl-5">{r.ruled_out.map((x) => <li key={x}>{x}</li>)}</ul>
            </section>
          )}

          <section aria-label="Incidents">
            <h2 className="mb-1 text-base font-semibold text-white print:text-black">Incidents</h2>
            {r.incidents.length === 0 ? <p className="text-slate-400">None in this period.</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr><th className={th}>Start</th><th className={th}>End</th><th className={th}>Duration</th>
                    <th className={th}>Affected</th><th className={th}>Most likely cause</th><th className={th}>Confidence</th></tr></thead>
                  <tbody>
                    {r.incidents.map((i) => (
                      <tr key={i.id}>
                        <td className={td}>{i.start}</td><td className={td}>{i.end}</td><td className={td}>{i.duration}</td>
                        <td className={td}>{i.failing.map((f) => f.name).join(', ')}</td>
                        <td className={td}>{i.cause}</td><td className={td}>{i.confidence} %</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {r.incidents.length > 0 && (
            <section aria-label="Evidence" className="space-y-4">
              <h2 className="text-base font-semibold text-white print:text-black">Evidence per incident</h2>
              {r.incidents.map((i) => (
                <div key={i.id} className="break-inside-avoid">
                  <h3 className="font-medium text-slate-100 print:text-black">{i.start} ({i.duration})</h3>
                  {i.causes[0] && (
                    <>
                      <p>{i.causes[0].title} ({i.causes[0].confidence} %)</p>
                      <ul className="list-disc pl-5 text-xs text-slate-400 print:text-black">
                        {i.causes[0].evidence.map((e) => <li key={e}>{e}</li>)}
                        {i.healthy.length > 0 && <li>Still working: {i.healthy.map((h) => h.name).join(', ')}</li>}
                      </ul>
                    </>
                  )}
                  {i.path_hops.map((p) => (
                    <div key={p.ts + p.host} className="mt-2">
                      <p className="text-xs text-slate-400 print:text-black">Path test to {p.host} at {p.ts}</p>
                      <div className="overflow-x-auto">
                        <table className="text-xs">
                          <thead><tr><th className={th}>Hop</th><th className={th}>Host</th><th className={th}>Loss</th><th className={th}>Avg</th></tr></thead>
                          <tbody>
                            {p.hops.map((h) => (
                              <tr key={h.hop} className={p.first_loss_hop === h.hop ? 'text-rose-300 print:font-bold print:text-black' : ''}>
                                <td className={td}>{h.hop}</td><td className={td}>{h.host ?? '???'}</td>
                                <td className={td}>{h.loss_pct} %</td><td className={td}>{fmtMs(h.avg)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </section>
          )}

          {r.targets.length > 0 && (
            <section aria-label="Measurements" className="break-inside-avoid">
              <h2 className="mb-1 text-base font-semibold text-white print:text-black">Measurements for the whole period</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead><tr><th className={th}>Target</th><th className={th}>Address</th><th className={th}>Layer</th>
                    <th className={th}>Probes</th><th className={th}>Loss</th><th className={th}>Median</th><th className={th}>95th pct</th></tr></thead>
                  <tbody>
                    {r.targets.map((t) => (
                      <tr key={t.name + t.host}>
                        <td className={td}>{t.name}</td><td className={td}>{t.host}</td><td className={td}>{t.layer_label}</td>
                        <td className={td}>{t.probes}</td><td className={td}>{t.loss_pct ?? '—'} %</td>
                        <td className={td}>{fmtMs(t.median_ms)}</td><td className={td}>{fmtMs(t.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {r.speeds.length > 0 && (
            <section aria-label="Speed tests" className="break-inside-avoid">
              <h2 className="mb-1 text-base font-semibold text-white print:text-black">Speed tests (iperf3)</h2>
              <div className="overflow-x-auto">
                <table className="text-xs">
                  <thead><tr><th className={th}>Type</th><th className={th}>Direction</th><th className={th}>Tests</th>
                    <th className={th}>Average</th><th className={th}>Min</th><th className={th}>Max</th></tr></thead>
                  <tbody>
                    {r.speeds.map((s) => (
                      <tr key={s.kind + s.direction}>
                        <td className={td}>{s.kind.toUpperCase()}</td><td className={td}>{s.direction}</td><td className={td}>{s.tests}</td>
                        <td className={td}>{fmtBps(s.avg_bps)}</td><td className={td}>{fmtBps(s.min_bps)}</td><td className={td}>{fmtBps(s.max_bps)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </article>
      )}
    </div>
  );
}
