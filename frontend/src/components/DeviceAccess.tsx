import { useEffect, useState } from 'react';
import { api, type DeviceAccess as Access } from '../lib/api';
import { fmtAgo } from '../lib/format';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const input = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** How to reach this device: an SSH link and its admin web page, from the exposure scan. NetWatch never logs in.
 * A remote server (a monitored target outside the network) is never scanned, so there everything is saved by hand. */
export function DeviceAccess({ id, remote = false }: { id: number; remote?: boolean }) {
  const [acc, setAcc] = useState<Access | null>(null);
  const [editing, setEditing] = useState(false);
  const [user, setUser] = useState('');
  const [port, setPort] = useState('22');
  const [mode, setMode] = useState<'auto' | 'on' | 'off'>('auto');
  const [admin, setAdmin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const apply = (a: Access) => {
    if (!a?.ssh || !a.web) return; // an unexpected reply (an older server): show nothing
    setAcc(a);
    setUser(a.ssh.user ?? '');
    setPort(String(a.ssh.port));
    setMode(a.ssh.mode);
    setAdmin(a.admin_url ?? '');
  };
  useEffect(() => {
    (remote ? api.targetAccess(id) : api.deviceAccess(id)).then(apply, (e) => setError(err(e)));
  }, [id, remote]);

  async function run(fn: () => Promise<Access>, done?: () => void) {
    setBusy(true);
    setError(null);
    try {
      apply(await fn());
      done?.();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }
  const save = () =>
    run(
      () => (remote ? api.saveTargetAccess : api.saveDeviceAccess)(id, { ssh_user: user.trim() || null, ssh_port: Number(port) || 22, ssh_mode: mode, admin_url: admin.trim() || null }),
      () => setEditing(false),
    );
  const check = () => run(() => api.checkDeviceAccess(id));

  async function copy(command: string) {
    try {
      await navigator.clipboard.writeText(command);
      setNote(`Copied: ${command}`);
    } catch {
      setNote(`Copy this: ${command}`);
    }
  }

  if (!acc?.ssh || !acc.web || !acc.ip) return error ? <p className="text-sm text-rose-400" role="alert">{error}</p> : null;
  const { ssh, web } = acc;
  const askUser = ssh.available && !ssh.user;

  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="Access">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Access</h2>
        <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => setEditing((v) => !v)}>
          {editing ? 'Close settings' : 'Settings'}
        </button>
      </div>
      <div className="space-y-3 text-sm">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Admin page</p>
          {web.primary ? (
            <div className="mt-1 space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <a href={web.primary.url} target="_blank" rel="noopener noreferrer"
                  className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>
                  Open admin page
                </a>
                <span className="break-all font-mono text-xs text-slate-400">{web.primary.url}</span>
              </div>
              {web.others.length > 0 && (
                <p className="text-xs text-slate-400">
                  Also:{' '}
                  {web.others.map((p, i) => (
                    <span key={p.url}>
                      {i > 0 && ', '}
                      <a href={p.url} target="_blank" rel="noopener noreferrer" className="text-sky-400 hover:underline">
                        {p.label}{p.port ? ` (${p.port})` : ''}
                      </a>
                    </span>
                  ))}
                </p>
              )}
            </div>
          ) : (
            <p className="mt-1 text-slate-500">
              {acc.scanned ? 'No web interface found at the last scan.' : 'No admin page address saved. Add one under Settings.'}
            </p>
          )}
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">SSH</p>
          {ssh.available && ssh.ssh_url && ssh.ssh_command ? (
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <a href={ssh.ssh_url} className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>Open in SSH app</a>
              <button className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => void copy(ssh.ssh_command!)}>
                Copy command
              </button>
              <span className="break-all font-mono text-xs text-slate-400">{ssh.ssh_command}</span>
            </div>
          ) : askUser ? (
            <p className="mt-1 text-slate-300">
              {acc.scanned ? `SSH is open${ssh.banner ? ` (${ssh.banner})` : ''}. Open ` : 'Open '}
              <button className="text-sky-400 hover:underline" onClick={() => setEditing(true)}>Settings</button> and
              enter the user you log in as.
            </p>
          ) : (
            <p className="mt-1 text-slate-500">
              {ssh.mode === 'off' ? 'SSH is hidden for this device.' : 'No SSH found at the last scan.'}
            </p>
          )}
          {note && <p className="mt-1 break-all font-mono text-xs text-emerald-300" role="status">{note}</p>}
        </div>
        {editing && (
          <div className="space-y-2 rounded-md bg-slate-950 p-3 ring-1 ring-slate-800">
            <div className="flex flex-wrap gap-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-400">SSH user</span>
                <input aria-label="SSH user" className={`${input} w-36`} maxLength={32} autoCapitalize="none" autoCorrect="off"
                  value={user} placeholder="root" onChange={(e) => setUser(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-400">SSH port</span>
                <input aria-label="SSH port" className={`${input} w-20`} inputMode="numeric" value={port}
                  onChange={(e) => setPort(e.target.value.replace(/\D/g, ''))} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-400">Show SSH</span>
                <select aria-label="Show SSH" className={input} value={mode} onChange={(e) => setMode(e.target.value as 'auto' | 'on' | 'off')}>
                  {acc.scanned ? (
                    <>
                      <option value="auto">When found by the scan</option>
                      <option value="on">Always</option>
                    </>
                  ) : (
                    <option value="auto">Show</option>
                  )}
                  <option value="off">Hide</option>
                </select>
              </label>
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-slate-400">Admin page address (optional, on {remote ? "this server's" : "this device's"} own address)</span>
              <input aria-label="Admin page address" className={`${input} w-full`} maxLength={200} autoCapitalize="none"
                value={admin} placeholder={acc.scanned ? `http://${acc.ip}:8080/ui` : `https://${acc.ip}/`} onChange={(e) => setAdmin(e.target.value)} />
            </label>
            <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy} onClick={() => void save()}>Save</button>
          </div>
        )}
        {acc.scanned && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-2 text-xs text-slate-500">
            <span>{acc.scanned_ms ? `Last scanned ${fmtAgo(acc.scanned_ms, Date.now())}.` : 'Not scanned yet.'}</span>
            <button className={`${btn} text-sky-300 ring-1 ring-slate-700 hover:bg-slate-800`} disabled={busy} onClick={() => void check()}>
              Check now
            </button>
          </div>
        )}
        {error && <p className="text-rose-400" role="alert">{error}</p>}
      </div>
    </section>
  );
}
