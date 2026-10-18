import { useState } from 'react';
import { Link } from 'react-router-dom';
import { scoreColour, ScoreRing } from '../components/ScoreRing';
import { api, type HealthScore, type WeeklyReport } from '../lib/api';
import { fmtTime } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';
const SPANS = [1, 7, 30];

function Section({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label={title}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

function Breakdown({ h }: { h: Pick<HealthScore, 'components'> }) {
  return (
    <ul className="space-y-2">
      {h.components.map((c) => (
        <li key={c.key} className="text-sm">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-slate-200">{c.label}</span>
            <span className="tabular-nums text-slate-300">{c.score}</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded bg-slate-800" aria-hidden>
            <div className="h-full rounded" style={{ width: `${c.score}%`, background: scoreColour(c.score) }} />
          </div>
          <div className="text-xs text-slate-500">{c.detail}</div>
        </li>
      ))}
    </ul>
  );
}

function ReportView({ r }: { r: WeeklyReport }) {
  const delta = r.previous_score != null && r.score != null ? r.score - r.previous_score : null;
  return (
    <div className="space-y-4 rounded-md bg-slate-950 p-3 ring-1 ring-slate-800">
      <div className="flex flex-wrap items-center gap-4">
        <ScoreRing score={r.score} size={72} />
        <div className="min-w-0 flex-1 text-sm">
          <p className="text-slate-100">{r.label}{delta != null && <span className={delta >= 0 ? 'text-emerald-300' : 'text-rose-300'}> ({delta >= 0 ? '+' : ''}{delta} vs the week before)</span>}</p>
          <p className="text-xs text-slate-500">{r.period.start} → {r.period.end}</p>
        </div>
        <a href={api.weeklyReportMarkdownUrl(r.week_start)} className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}>Download (.md)</a>
      </div>
      <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">{r.highlights.map((h, i) => <li key={i}>{h}</li>)}</ul>
      <Breakdown h={r} />
      <div className="text-sm text-slate-300">
        Internet: {r.summary.isp_availability_pct} % available · {r.summary.incidents} incident{r.summary.incidents === 1 ? '' : 's'} ({r.summary.isp_incidents} on the Bell network) · longest {r.summary.longest}
      </div>
      {r.incidents.length > 0 && (
        <ul className="divide-y divide-slate-800 text-sm">
          {r.incidents.map((i) => (
            <li key={i.id} className="flex flex-wrap justify-between gap-2 py-1">
              <span className="text-slate-300">{i.start} · {i.cause}</span>
              <span className="text-xs text-slate-500">{i.duration}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Network health score now, and the weekly health reports. */
export function Health() {
  const [days, setDays] = useState(7);
  const score = usePolling(() => api.score(days), 300_000, [days]);
  const reports = usePolling(api.weeklyReports, 300_000);
  const [open, setOpen] = useState<WeeklyReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const show = async (week: string) => {
    setErr(null);
    try {
      setOpen(open?.week_start === week ? null : await api.weeklyReport(week));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  const generate = async () => {
    setBusy(true);
    setErr(null);
    try {
      setOpen(await api.generateWeeklyReport());
      await reports.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const h = score.data;
  const rows = reports.data?.reports ?? [];
  const max = Math.max(1, ...rows.map((r) => r.score ?? 0));
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-white">Network health</h1>
      <Section
        title="Health score"
        right={
          <div className="inline-flex overflow-hidden rounded ring-1 ring-slate-700" role="group" aria-label="Score period">
            {SPANS.map((d) => (
              <button key={d} onClick={() => setDays(d)} aria-pressed={d === days} className={`px-2 py-0.5 text-xs ${d === days ? 'bg-sky-600 text-white' : 'text-slate-300 hover:bg-slate-800'}`}>
                {d === 1 ? '24 h' : `${d} days`}
              </button>
            ))}
          </div>
        }
      >
        {score.error && <p className="text-sm text-rose-400">{score.error}</p>}
        {!h ? <p className="text-sm text-slate-500">Loading…</p> : (
          <div className="grid gap-6 md:grid-cols-[auto_1fr]">
            <div className="flex flex-col items-center gap-1">
              <ScoreRing score={h.score} size={128} />
              <span className="text-sm font-medium" style={{ color: scoreColour(h.score) }}>{h.label}</span>
            </div>
            <div className="space-y-4">
              <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">{h.highlights.map((x, i) => <li key={i}>{x}</li>)}</ul>
              <Breakdown h={h} />
              <p className="text-xs text-slate-500">
                Areas without data (no Home Hub, no bufferbloat test or <Link to="/exposure" className="text-sky-400 hover:underline">exposure scan</Link> yet) are left out of the score.
              </p>
            </div>
          </div>
        )}
      </Section>

      <Section
        title="Weekly reports"
        right={<button className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`} disabled={busy} onClick={() => void generate()}>{busy ? 'Writing…' : 'Write last week now'}</button>}
      >
        <p className="mb-3 text-xs text-slate-400">Written every Monday morning for the week before (Monday to Sunday) and kept for two years.</p>
        {err && <p className="mb-2 text-sm text-rose-400">{err}</p>}
        {rows.length === 0 ? <p className="text-sm text-slate-500">No weekly report yet.</p> : (
          <ul className="space-y-2">
            {rows.map((r) => (
              <li key={r.week_start}>
                <button onClick={() => void show(r.week_start)} aria-expanded={open?.week_start === r.week_start} className="flex w-full items-center gap-3 rounded-md px-2 py-1.5 text-left text-sm hover:bg-slate-800/60">
                  <span className="shrink-0 whitespace-nowrap text-slate-300">Week of {r.week_start}</span>
                  <span className="h-2 flex-1 overflow-hidden rounded bg-slate-800" aria-hidden>
                    <span className="block h-full rounded" style={{ width: `${(100 * (r.score ?? 0)) / max}%`, background: scoreColour(r.score) }} />
                  </span>
                  <span className="w-20 shrink-0 text-right tabular-nums text-slate-200">{r.score ?? '—'} <span className="text-xs text-slate-500">{r.label}</span></span>
                </button>
                {open?.week_start === r.week_start && <div className="mt-2"><ReportView r={open} /></div>}
              </li>
            ))}
          </ul>
        )}
        {rows[0] && <p className="mt-2 text-xs text-slate-500">Last written {fmtTime(rows[0].created_ms)}.</p>}
      </Section>
    </div>
  );
}
