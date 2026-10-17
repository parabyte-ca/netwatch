import { useEffect, useRef, useState, type ReactNode } from 'react';
import { EventRow } from '../components/EventRow';
import { api, type SettingField, type SettingsResponse } from '../lib/api';
import { fmtAgo, fmtBytes, fmtTime } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';
const input =
  'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';

function Card({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label={title}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Wait for NetWatch to go down and come back after a restart, then reload the page. */
async function waitForRestart(setNote: (s: string) => void) {
  setNote('Restarting… waiting for NetWatch to come back.');
  const start = Date.now();
  let wentDown = false;
  while (Date.now() - start < 120_000) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const r = await fetch('/health', { cache: 'no-store' });
      if (r.ok && (wentDown || Date.now() - start > 8000)) {
        window.location.reload();
        return;
      }
      if (!r.ok) wentDown = true;
    } catch {
      wentDown = true;
    }
  }
  setNote('NetWatch has not come back after 2 minutes. Check the container on TrueNAS.');
}

function FieldInput({ f, value, onChange }: { f: SettingField; value: unknown; onChange: (v: unknown) => void }) {
  const id = `set-${f.name}`;
  if (f.type === 'bool') {
    return <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
  }
  if (f.type === 'choice') {
    return (
      <select id={id} className={input} value={String(value)} onChange={(e) => onChange(e.target.value)}>
        {f.choices?.map((c) => <option key={c}>{c}</option>)}
      </select>
    );
  }
  if (f.type === 'int' || f.type === 'float') {
    return (
      <input id={id} type="number" className={`${input} w-28`} min={f.min} max={f.max} step={f.type === 'int' ? 1 : 'any'}
        value={String(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
    );
  }
  return (
    <input id={id} type={f.secret ? 'password' : 'text'} className={`${input} w-full max-w-md`} value={String(value)}
      placeholder={f.secret ? 'not set' : ''} autoComplete="off" onChange={(e) => onChange(e.target.value)} />
  );
}

function SettingsCard({ data, onSaved }: { data: SettingsResponse; onSaved: (d: SettingsResponse) => void }) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const groups = [...new Map(data.fields.map((f) => [f.group, f.group_label])).entries()];
  const dirty = Object.keys(draft);

  async function save(changes: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const res = await api.saveSettings(changes);
      setDraft({});
      onSaved(res);
      setSaved('Saved.');
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Settings"
      actions={
        <div className="flex items-center gap-3">
          {saved && <span className="text-xs text-emerald-300">{saved}</span>}
          <button className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`} disabled={busy || !dirty.length} onClick={() => setDraft({})}>
            Discard
          </button>
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy || !dirty.length} onClick={() => void save(draft)}>
            Save {dirty.length ? `(${dirty.length})` : ''}
          </button>
        </div>
      }
    >
      <p className="mb-3 text-xs text-slate-500">
        Values saved here override the .env file. Settings marked "restart" take effect after NetWatch restarts.
      </p>
      {error && <p className="mb-3 text-sm text-rose-400">{error}</p>}
      <div className="space-y-5">
        {groups.map(([group, label]) => (
          <fieldset key={group}>
            <legend className="mb-2 text-sm font-semibold text-slate-200">{label}</legend>
            <div className="divide-y divide-slate-800">
              {data.fields.filter((f) => f.group === group).map((f) => {
                const value = f.name in draft ? draft[f.name] : f.secret ? '' : f.value;
                return (
                  <div key={f.name} className="grid gap-2 py-2 sm:grid-cols-[16rem_1fr] sm:items-center">
                    <label htmlFor={`set-${f.name}`} className="text-sm text-slate-300">
                      {f.label}
                      {!f.live && <span className="ml-2 rounded bg-amber-900/50 px-1.5 text-[10px] text-amber-200">restart</span>}
                    </label>
                    <div className="flex flex-wrap items-center gap-2">
                      <FieldInput f={f} value={value} onChange={(v) => setDraft((d) => ({ ...d, [f.name]: v }))} />
                      {f.unit && <span className="text-xs text-slate-500">{f.unit}</span>}
                      {f.overridden && (
                        <button className="text-xs text-sky-400 hover:underline" disabled={busy}
                          onClick={() => void save({ [f.name]: null })} title={`Default: ${String(f.default)}`}>
                          reset to default
                        </button>
                      )}
                      {f.help && <span className="basis-full text-xs text-slate-500">{f.help}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
    </Card>
  );
}

function SessionsCard() {
  const { data, error, refresh } = usePolling(api.sessions, 60_000);
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await refresh();
    } catch (e) {
      setMsg(err(e));
    }
  };
  return (
    <Card
      title="Signed-in sessions"
      actions={
        <button className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`} disabled={!data || data.length < 2}
          onClick={() => void act(api.revokeOtherSessions)}>
          Sign out all other sessions
        </button>
      }
    >
      {(error || msg) && <p className="mb-2 text-sm text-rose-400">{error ?? msg}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr><th className="py-1">User</th><th>Address</th><th>Browser</th><th>Signed in</th><th>Last active</th><th /></tr>
          </thead>
          <tbody>
            {data?.map((s) => (
              <tr key={s.id} className="border-t border-slate-800">
                <td className="py-1 text-slate-200">{s.username}{s.current && <span className="ml-2 text-xs text-emerald-300">this browser</span>}</td>
                <td className="font-mono text-xs text-slate-400">{s.ip}</td>
                <td className="max-w-xs truncate text-xs text-slate-400" title={s.user_agent}>{s.user_agent}</td>
                <td className="text-xs text-slate-400">{fmtTime(s.created_ms)}</td>
                <td className="text-xs text-slate-400">{fmtAgo(s.last_seen_ms, Date.now())}</td>
                <td className="text-right">
                  {!s.current && (
                    <button className="text-xs text-rose-400 hover:underline" onClick={() => void act(() => api.revokeSession(s.id))}>
                      Sign out
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function BackupsCard({ onRestart }: { onRestart: () => void }) {
  const { data, error, refresh } = usePolling(api.backups, 60_000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function act(fn: () => Promise<unknown>, restartAfter = false) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      if (restartAfter) onRestart();
      else await refresh();
    } catch (e) {
      setMsg(err(e));
    } finally {
      setBusy(false);
    }
  }

  const confirmRestore = (what: string) =>
    confirm(
      `Restore ${what}?\n\nNetWatch will restart and replace ALL current data (targets, history, devices, ` +
        'settings and sign-in accounts) with the backup. A safety copy of the current database is kept.',
    );

  return (
    <Card
      title="Backup and restore"
      actions={
        <div className="flex flex-wrap gap-2">
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy} onClick={() => void act(api.createBackup)}>
            Back up now
          </button>
          <button className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`} disabled={busy} onClick={() => fileRef.current?.click()}>
            Restore from file…
          </button>
          <input ref={fileRef} type="file" accept=".db,.gz,.sqlite,.sqlite3" className="hidden" aria-label="Backup file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f && confirmRestore(f.name)) void act(() => api.restoreUpload(f), true);
            }} />
        </div>
      }
    >
      {(error || msg) && <p className="mb-2 text-sm text-rose-400">{error ?? msg}</p>}
      {data && (
        <p className="mb-2 text-xs text-slate-500">
          Database size {fmtBytes(data.database_bytes)}. Backups are consistent snapshots taken while NetWatch runs;
          updates also take one automatically. They are kept in data/backups on TrueNAS.
        </p>
      )}
      {data?.restore_pending && <p className="mb-2 text-sm text-amber-300">A restore is waiting for the next restart.</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr><th className="py-1">File</th><th>Size</th><th>Taken</th><th /></tr>
          </thead>
          <tbody>
            {data?.backups.map((b) => (
              <tr key={b.name} className="border-t border-slate-800">
                <td className="py-1 font-mono text-xs text-slate-200">{b.name}</td>
                <td className="text-xs text-slate-400">{fmtBytes(b.bytes)}</td>
                <td className="text-xs text-slate-400">{fmtTime(b.modified * 1000)}</td>
                <td className="space-x-3 text-right text-xs">
                  <a className="text-sky-400 hover:underline" href={api.backupUrl(b.name)}>Download</a>
                  <button className="text-amber-300 hover:underline" disabled={busy}
                    onClick={() => confirmRestore(b.name) && void act(() => api.restoreBackup(b.name), true)}>
                    Restore
                  </button>
                  <button className="text-rose-400 hover:underline" disabled={busy}
                    onClick={() => confirm(`Delete ${b.name}?`) && void act(() => api.deleteBackup(b.name))}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && data.backups.length === 0 && <p className="text-sm text-slate-500">No backups yet.</p>}
    </Card>
  );
}

function SecurityLogCard() {
  const { data } = usePolling(api.securityLog, 60_000);
  return (
    <Card title="Security log (30 days)">
      {data?.length ? (
        <div className="max-h-80 overflow-y-auto">{data.map((e) => <EventRow key={e.id} ev={e} />)}</div>
      ) : (
        <p className="text-sm text-slate-500">No sign-ins or admin actions recorded yet.</p>
      )}
    </Card>
  );
}

export function Admin() {
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.adminSettings().then(setSettings, (e) => setError(err(e)));
  }, []);

  const restart = async () => {
    try {
      await api.restart();
    } catch (e) {
      setError(err(e));
      return;
    }
    void waitForRestart(setNote);
  };

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-white">Admin</h1>
      {note && <p className="rounded-md bg-sky-950 p-3 text-sm text-sky-200 ring-1 ring-sky-800">{note}</p>}
      {settings && settings.restart_pending.length > 0 && !note && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-amber-950/60 p-3 text-sm text-amber-200 ring-1 ring-amber-800">
          <span>
            Restart NetWatch to apply:{' '}
            {settings.restart_pending.map((n) => settings.fields.find((f) => f.name === n)?.label ?? n).join(', ')}.
            Monitoring pauses for a few seconds.
          </span>
          <button className={`${btn} bg-amber-600 text-white hover:bg-amber-500`} onClick={() => void restart()}>
            Restart now
          </button>
        </div>
      )}
      {error && <p className="text-rose-400">{error}</p>}
      {settings && <SettingsCard data={settings} onSaved={setSettings} />}
      <SessionsCard />
      <BackupsCard onRestart={() => void waitForRestart(setNote)} />
      <SecurityLogCard />
    </div>
  );
}
