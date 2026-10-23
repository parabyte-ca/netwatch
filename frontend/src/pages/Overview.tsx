import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { EventRow } from '../components/EventRow';
import { StatusBadge } from '../components/StatusBadge';
import { api, toInput, type State, type Target, type TargetStatus } from '../lib/api';
import { fmtAgo, fmtMs, fmtPct, GROUP_LABEL, GROUP_ORDER, STATE_STYLE } from '../lib/format';
import { usePolling } from '../lib/usePolling';
import { ScoreRing } from '../components/ScoreRing';
import { TopTalkers } from '../components/TopTalkers';

/** A target's name that turns into a text box when clicked (Enter saves, Esc cancels). */
function EditableName({ t, onSaved }: { t: Target; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(t.name);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function save(e?: FormEvent) {
    e?.preventDefault();
    const next = name.trim();
    if (!next || next === t.name) {
      setEditing(false);
      setName(t.name);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.updateTarget(t.id, { ...toInput(t), name: next });
      setEditing(false);
      onSaved();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setBusy(false);
    }
  }

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => { setName(t.name); setEditing(true); }}
        className="group relative z-10 flex max-w-full items-center gap-1 text-left font-medium text-white hover:text-sky-300"
        title="Rename"
      >
        <span className="truncate">{t.name}</span>
        <span aria-hidden className="text-xs text-slate-500 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100">✎</span>
        <span className="sr-only">(rename)</span>
      </button>
    );
  }
  return (
    <form onSubmit={save} className="relative z-10">
      <input
        aria-label={`New name for ${t.name}`}
        className="w-full rounded border border-sky-600 bg-slate-950 px-1.5 py-0.5 text-sm text-white focus:outline-none"
        value={name}
        maxLength={100}
        autoFocus
        disabled={busy}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => void save()}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setName(t.name);
            setEditing(false);
          }
        }}
      />
      {err && <div className="mt-1 text-xs text-rose-400">{err}</div>}
    </form>
  );
}

function Tile({ t, s, now, onRenamed }: { t: Target; s: TargetStatus | undefined; now: number; onRenamed: () => void }) {
  const state: State = s?.state ?? 'unknown';
  const st = s?.stats;
  return (
    <div className={`relative rounded-lg bg-slate-900 p-4 ring-1 transition hover:bg-slate-800/80 ${STATE_STYLE[state].ring}`}>
      {/* The whole tile opens the target's graphs; the name above it is a rename button. */}
      <Link to={`/targets/${t.id}`} className="absolute inset-0 rounded-lg" aria-label={`Open ${t.name}`} />
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <EditableName t={t} onSaved={onRenamed} />
          <div className="truncate text-xs text-slate-500">
            {t.kind.toUpperCase()} · {t.kind === 'http' ? t.url : t.port ? `${t.host}:${t.port}` : t.host}
          </div>
        </div>
        <StatusBadge state={state} />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-slate-500">Last</dt>
          <dd className="font-mono text-slate-200">
            {s?.last ? (s.last.ok ? fmtMs(s.last.rtt_ms) : (s.last.error ?? 'fail')) : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">Median</dt>
          <dd className="font-mono text-slate-200">{fmtMs(st?.median)}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Loss</dt>
          <dd className={`font-mono ${!st?.lost ? 'text-slate-200' : (st.loss_pct ?? 0) > 15 ? 'text-rose-300' : 'text-amber-300'}`}>{fmtPct(st?.loss_pct)}</dd>
        </div>
      </dl>
      {s?.last && <div className="mt-2 text-[11px] text-slate-600">updated {fmtAgo(s.last.ts_ms, now)}</div>}
    </div>
  );
}

export function Overview() {
  const targets = usePolling(api.targets, 30_000);
  const status = usePolling(api.status, 3_000);
  const active = usePolling(api.activeEvents, 5_000);
  const score = usePolling(() => api.score(7), 300_000);
  const recent = usePolling(() => api.events({ start: Math.floor(Date.now() / 1000) - 86_400, limit: 6, kind: ['outage', 'degraded', 'nic_link', 'nic_speed', 'nic_errors'] }), 30_000);

  if (targets.error) return <p className="text-rose-400">Could not load targets: {targets.error}</p>;
  if (!targets.data) return <p className="text-slate-500">Loading…</p>;

  const enabled = targets.data.filter((t) => t.enabled);
  const byId = new Map(status.data?.targets.map((s) => [s.id, s]));
  const now = status.data?.now_ms ?? Date.now();
  const down = enabled.filter((t) => byId.get(t.id)?.state === 'down');
  const degraded = enabled.filter((t) => byId.get(t.id)?.state === 'degraded');

  if (enabled.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-400">
        No targets are being monitored. <Link to="/targets" className="text-sky-400 hover:underline">Add one</Link>.
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <section
        aria-label="Health summary"
        className={`rounded-lg p-4 ring-1 ${
          down.length ? 'bg-rose-950/40 ring-rose-800' : degraded.length ? 'bg-amber-950/30 ring-amber-800/60' : 'bg-emerald-950/30 ring-emerald-800/50'
        }`}
      >
        {typeof score.data?.score === 'number' && (
          <Link to="/health-score" className="float-right ml-3 flex items-center gap-2 rounded-md px-1 text-xs text-slate-300 hover:bg-slate-900/40" title="Network health score, last 7 days">
            <ScoreRing score={score.data.score} size={48} />
            <span className="hidden sm:inline">{score.data.label}<br /><span className="text-slate-500">7-day health</span></span>
          </Link>
        )}
        <div className="text-lg font-medium text-white">
          {down.length
            ? `${down.length} target${down.length > 1 ? 's' : ''} down`
            : degraded.length
              ? `Packet loss on ${degraded.length} target${degraded.length > 1 ? 's' : ''}`
              : 'All targets responding'}
        </div>
        <div className="mt-1 text-sm text-slate-400">
          {[...down, ...degraded].map((t) => t.name).join(', ') ||
            `${enabled.length} targets monitored.`}
        </div>
        {status.error && <div className="mt-1 text-sm text-rose-400">Live status unavailable: {status.error}</div>}
      </section>

      {(active.data?.length ?? 0) > 0 && (
        <section aria-label="Active incidents" className="rounded-lg bg-slate-900 p-2 ring-1 ring-rose-900/60">
          <h2 className="px-2 pb-1 text-xs font-semibold uppercase tracking-wide text-rose-300">Active incidents</h2>
          {active.data!.map((ev) => <EventRow key={ev.id} ev={ev} />)}
        </section>
      )}

      {GROUP_ORDER.map((g) => {
        const items = enabled.filter((t) => t.group === g);
        if (!items.length) return null;
        return (
          <section key={g} aria-label={GROUP_LABEL[g]}>
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">{GROUP_LABEL[g]}</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {items.map((t) => (
                <Tile key={t.id} t={t} s={byId.get(t.id)} now={now} onRenamed={() => void targets.refresh()} />
              ))}
            </div>
          </section>
        );
      })}
      <TopTalkers />
      <section aria-label="Recent events" className="rounded-lg bg-slate-900 p-2 ring-1 ring-slate-800">
        <div className="flex items-center justify-between px-2 pb-1">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Last 24 hours</h2>
          <Link to="/timeline" className="text-xs text-sky-400 hover:underline">Timeline →</Link>
        </div>
        {recent.data?.length ? recent.data.map((ev) => <EventRow key={ev.id} ev={ev} />) : (
          <p className="px-2 py-1 text-sm text-slate-500">No outages, packet loss or NIC problems.</p>
        )}
      </section>
    </div>
  );
}
