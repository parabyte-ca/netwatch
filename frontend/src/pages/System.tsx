import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useAuth } from '../components/AuthGate';
import { api, type BumpKind, type Release, type UpdateMode, type UpdateSummary } from '../lib/api';
import { usePolling } from '../lib/usePolling';

const MODE_LABEL: Record<UpdateMode, string> = {
  off: 'Off (never check)',
  notify: 'Notify only',
  patch: 'Install bug-fix releases automatically',
  minor: 'Install bug-fix and feature releases automatically',
};

const KIND_STYLE: Record<BumpKind, { label: string; cls: string }> = {
  patch: { label: 'Bug fix', cls: 'bg-emerald-900/60 text-emerald-200' },
  minor: { label: 'Features', cls: 'bg-sky-900/60 text-sky-200' },
  major: { label: 'Major', cls: 'bg-amber-900/60 text-amber-200' },
};

const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';
const input =
  'rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';

function Card({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label={title}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

function fmtWhen(v: number | string | null | undefined): string {
  if (!v) return '—';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString('en-CA');
}

/** Minimal renderer for CHANGELOG-style notes: ### headings and - bullets. */
export function ReleaseNotes({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => {
    if (bullets.length) {
      blocks.push(
        <ul key={`ul${blocks.length}`} className="ml-5 list-disc space-y-0.5 text-sm text-slate-300">
          {bullets.map((b, i) => <li key={i}>{b}</li>)}
        </ul>,
      );
      bullets = [];
    }
  };
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith('- ') || line.startsWith('* ')) {
      bullets.push(line.slice(2));
      continue;
    }
    flush();
    if (line.startsWith('#')) {
      blocks.push(<h4 key={`h${blocks.length}`} className="mt-2 text-xs font-semibold uppercase text-slate-400">{line.replace(/^#+\s*/, '')}</h4>);
    } else {
      blocks.push(<p key={`p${blocks.length}`} className="text-sm text-slate-300">{line}</p>);
    }
  }
  flush();
  return <div className="space-y-1">{blocks.length ? blocks : <p className="text-sm text-slate-500">No release notes.</p>}</div>;
}

export function System() {
  const [active, setActive] = useState(false);
  const { data, error, refresh } = usePolling(api.updates, active ? 2_000 : 30_000);
  const [log, setLog] = useState('');
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const startVersion = useRef<string | null>(null);

  const state = data?.progress.state ?? 'idle';
  useEffect(() => {
    if (data && startVersion.current === null) startVersion.current = data.current;
    setActive(state === 'requested' || state === 'running');
  }, [data, state]);

  // Follow the update log while an update runs.
  useEffect(() => {
    if (!active && state !== 'failed' && state !== 'succeeded') return;
    let stop = false;
    const tick = async () => {
      try {
        const text = await api.upgradeLog();
        if (!stop) setLog(text);
      } catch {
        /* the app restarts mid-update; keep trying */
      }
    };
    void tick();
    const id = setInterval(tick, 2_000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [active, state]);

  // After a successful update the server runs new code: reload to pick up the new UI.
  const upgraded = data && startVersion.current && data.current !== startVersion.current;
  useEffect(() => {
    if (!upgraded) return;
    const id = setTimeout(() => window.location.reload(), 4_000);
    return () => clearTimeout(id);
  }, [upgraded]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      setMessage({ ok: true, text: ok });
      await refresh();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return error ? <p className="text-rose-400">{error}</p> : <p className="text-slate-500">Loading…</p>;
  }

  const install = (r: Release) => {
    if (window.confirm(`Install NetWatch v${r.version}? The database is backed up first, and the update rolls back automatically if the new version is unhealthy.`)) {
      void act(() => api.applyUpdate(r.version), `Update to v${r.version} requested. The upgrade agent picks it up within a minute.`);
    }
  };

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-white">System</h1>
      {upgraded && (
        <p role="status" className="rounded-md bg-emerald-950/60 px-3 py-2 text-sm text-emerald-200">
          Updated to v{data.current}. Reloading…
        </p>
      )}
      {error && active && (
        <p role="status" className="rounded-md bg-sky-950/60 px-3 py-2 text-sm text-sky-200">
          NetWatch is restarting with the new version…
        </p>
      )}
      {message && (
        <p role="alert" className={`rounded-md px-3 py-2 text-sm ${message.ok ? 'bg-emerald-950/60 text-emerald-200' : 'bg-rose-950/60 text-rose-300'}`}>
          {message.text}
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card
          title="Version"
          actions={
            data.check_enabled && (
              <button disabled={busy} onClick={() => void act(api.checkUpdates, 'Checked for updates.')} className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}>
                Check now
              </button>
            )
          }
        >
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-slate-500">Running</dt>
            <dd className="font-mono text-white">v{data.current}</dd>
            <dt className="text-slate-500">Latest release</dt>
            <dd className="font-mono text-slate-200">{data.latest ? `v${data.latest}` : '—'}</dd>
            <dt className="text-slate-500">Last checked</dt>
            <dd className="text-slate-300">{data.check_enabled ? fmtWhen(data.checked_at) : 'checks disabled'}</dd>
          </dl>
          {data.check_error && <p className="mt-2 text-xs text-amber-300">Last check failed: {data.check_error}</p>}
          {!data.update_available && data.checked_at && <p className="mt-3 text-sm text-emerald-300">You're up to date.</p>}
        </Card>

        <Card title="Upgrade agent">
          {data.helper.connected ? (
            <p className="text-sm text-emerald-300">Connected · last seen {fmtWhen(data.helper.last_seen)}</p>
          ) : (
            <div className="space-y-2 text-sm text-slate-300">
              <p className="text-amber-300">Not connected, so updates can't be installed from here.</p>
              <p>
                On TrueNAS, run <code className="rounded bg-slate-800 px-1">sudo scripts/upgrade-agent.sh --install</code> in the
                NetWatch folder. It adds a TrueNAS cron job (System → Advanced → Cron Jobs) that survives OS upgrades.
              </p>
            </div>
          )}
        </Card>
      </div>

      {(state !== 'idle' || log) && (
        <Card title="Update progress">
          <ProgressLine summary={data} />
          {log && (
            <pre className="mt-3 max-h-80 overflow-auto rounded-md bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-300" aria-label="Update log">
              {log}
            </pre>
          )}
        </Card>
      )}

      {data.available.length > 0 && (
        <Card title="Available updates">
          <div className="space-y-4">
            {data.available.map((r, i) => (
              <article key={r.version} className="rounded-md border border-slate-800 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-white">v{r.version}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] ${KIND_STYLE[r.kind].cls}`}>{KIND_STYLE[r.kind].label}</span>
                    <span className="text-xs text-slate-500">{fmtWhen(r.published_at)}</span>
                  </div>
                  {i === 0 && (
                    <button
                      disabled={busy || active || !data.helper.connected}
                      onClick={() => install(r)}
                      className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}
                      title={data.helper.connected ? undefined : 'Upgrade agent not connected'}
                    >
                      Install v{r.version}
                    </button>
                  )}
                </div>
                {data.auto_target === r.version && (
                  <p className="mt-1 text-xs text-emerald-300">
                    Installs automatically at {data.policy.window} ({data.timezone}).
                  </p>
                )}
                <div className="mt-2">
                  <ReleaseNotes text={r.notes} />
                </div>
              </article>
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <PolicyCard summary={data} onSaved={refresh} />
        <Card
          title="History"
          actions={
            <button
              disabled={busy || active || !data.helper.connected || !data.rollback_available}
              onClick={() => {
                if (window.confirm('Roll back to the version before the last update? This also restores the database backup taken before it, so data recorded since then is lost.')) {
                  void act(api.rollback, 'Rollback requested.');
                }
              }}
              className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}
            >
              Roll back last update
            </button>
          }
        >
          {data.history.length === 0 ? (
            <p className="text-sm text-slate-500">No updates yet.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-1">When</th><th>Change</th><th>Result</th><th>By</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {data.history.map((h, i) => (
                  <tr key={h.id ?? i}>
                    <td className="py-1.5 text-slate-400">{fmtWhen(h.finished_at ?? h.started_at)}</td>
                    <td className="font-mono text-xs text-slate-200">
                      {h.action === 'rollback' ? `rollback v${h.from_version} → v${h.to_version}` : `v${h.from_version} → v${h.version ?? h.to_version}`}
                    </td>
                    <td className={h.state === 'succeeded' ? 'text-emerald-300' : 'text-rose-300'}>
                      {h.state === 'succeeded' ? 'OK' : h.rolled_back === 'yes' ? 'Failed, rolled back' : 'Failed'}
                    </td>
                    <td className="text-slate-400">{h.reason === 'auto' ? 'auto' : h.requested_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <AccountCard />
    </div>
  );
}

function ProgressLine({ summary }: { summary: UpdateSummary }) {
  const p = summary.progress;
  const target = p.request?.version ?? p.status?.version;
  const what = (p.request?.action ?? p.status?.action) === 'rollback' ? 'Rollback' : `Update${target ? ` to v${target}` : ''}`;
  switch (p.state) {
    case 'requested':
      return <p className="text-sm text-sky-200">{what} requested; waiting for the upgrade agent (up to a minute)…</p>;
    case 'running':
      return <p className="text-sm text-sky-200">{what} in progress. NetWatch will restart; this page reconnects automatically.</p>;
    case 'succeeded':
      return <p className="text-sm text-emerald-300">{what} completed ({fmtWhen(p.status?.finished_at)})</p>;
    case 'failed':
      return (
        <p className="text-sm text-rose-300">
          {what} failed{p.status?.rolled_back === 'yes' ? ' and was rolled back automatically' : ''}
          {p.reason ? `: ${p.reason}` : p.status?.error ? `: ${p.status.error}` : '.'} See the log below.
        </p>
      );
    default:
      return null;
  }
}

function PolicyCard({ summary, onSaved }: { summary: UpdateSummary; onSaved: () => Promise<void> }) {
  const [mode, setMode] = useState<UpdateMode>(summary.policy.mode);
  const [window_, setWindow] = useState(summary.policy.window);
  const [note, setNote] = useState<string | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await api.setUpdatePolicy(mode, window_);
      setNote('Saved.');
      await onSaved();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Card title="Automatic updates">
      <form onSubmit={save} className="space-y-3" aria-label="Automatic update policy">
        <div>
          <label htmlFor="upd-mode" className="mb-1 block text-xs text-slate-400">Policy</label>
          <select id="upd-mode" className={`${input} w-full`} value={mode} onChange={(e) => setMode(e.target.value as UpdateMode)}>
            {(Object.keys(MODE_LABEL) as UpdateMode[]).map((m) => (
              <option key={m} value={m}>{MODE_LABEL[m]}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="upd-window" className="mb-1 block text-xs text-slate-400">Maintenance window start ({summary.timezone})</label>
          <input id="upd-window" type="time" className={input} value={window_} onChange={(e) => setWindow(e.target.value)} required />
        </div>
        <p className="text-xs text-slate-500">
          Automatic installs run within an hour of the window start. Major releases, and anything your policy doesn't
          cover, always wait for you to press Install. A failed automatic update is rolled back and not retried.
        </p>
        <div className="flex items-center gap-3">
          <button type="submit" className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>Save</button>
          {note && <span className="text-xs text-slate-400">{note}</span>}
        </div>
      </form>
    </Card>
  );
}

function AccountCard() {
  const { username, logout } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.changePassword(current, next);
      setCurrent('');
      setNext('');
      setNote({ ok: true, text: 'Password changed. Other sessions were signed out.' });
    } catch (err) {
      setNote({ ok: false, text: err instanceof Error ? err.message : String(err) });
    }
  }

  return (
    <Card title="Account" actions={<button onClick={() => void logout()} className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}>Sign out</button>}>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3" aria-label="Change password">
        <p className="w-full text-sm text-slate-400">Signed in as <span className="text-slate-200">{username}</span>.</p>
        <div>
          <label htmlFor="pw-current" className="mb-1 block text-xs text-slate-400">Current password</label>
          <input id="pw-current" type="password" autoComplete="current-password" className={input} value={current} onChange={(e) => setCurrent(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="pw-new" className="mb-1 block text-xs text-slate-400">New password (10+ characters)</label>
          <input id="pw-new" type="password" autoComplete="new-password" minLength={10} className={input} value={next} onChange={(e) => setNext(e.target.value)} required />
        </div>
        <button type="submit" className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}>Change password</button>
        {note && <p className={`w-full text-sm ${note.ok ? 'text-emerald-300' : 'text-rose-300'}`}>{note.text}</p>}
      </form>
    </Card>
  );
}
