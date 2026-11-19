import { useEffect, useState } from 'react';
import { api, type SavedServer } from '../lib/api';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const input = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

interface Draft {
  id: number | null;
  name: string;
  host: string;
  port: string;
  username: string;
  notes: string;
}

const blank: Draft = { id: null, name: '', host: '', port: '22', username: '', notes: '' };

/** Admin → Servers: machines to open in an SSH app. NetWatch keeps no login for them. */
export function ServersCard() {
  const [servers, setServers] = useState<SavedServer[] | null>(null);
  const [suggestions, setSuggestions] = useState<{ host: string; label: string }[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setServers((await api.servers()).servers);
    api.serverSuggestions().then((r) => setSuggestions(r.suggestions), () => undefined);
  };
  useEffect(() => {
    load().catch((e) => setError(err(e)));
  }, []);

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    const body = {
      name: draft.name.trim(),
      host: draft.host.trim(),
      port: Number(draft.port),
      username: draft.username.trim(),
      notes: draft.notes.trim() || null,
    };
    try {
      if (draft.id === null) await api.createServer(body);
      else await api.saveServer(draft.id, body);
      setDraft(null);
      await load();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy(s: SavedServer) {
    try {
      await navigator.clipboard.writeText(s.ssh_command);
      setNote(`Copied: ${s.ssh_command}`);
    } catch {
      setNote(`Copy this: ${s.ssh_command}`);
    }
  }

  const known = new Set((servers ?? []).map((s) => s.host));
  const set = (k: keyof Draft, v: string) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  const prefill = (host: string) => {
    const hit = suggestions.find((x) => x.host === host);
    setDraft((d) => (d ? { ...d, host, name: d.name || (hit?.label ?? '') } : d));
  };

  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="Servers">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Servers</h2>
        {!draft && (
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} onClick={() => setDraft({ ...blank })}>
            Add a server
          </button>
        )}
      </div>
      <div className="space-y-3 text-sm">
        <p className="text-xs text-slate-400">
          Machines you log in to, such as AdGuard Home or TrueNAS. <strong>Open in SSH app</strong> hands an ssh:// link to an
          SSH app on this phone or computer (Termius, Blink, JuiceSSH and similar), which asks for the key or password.
          NetWatch stores no login and has no terminal of its own. Only addresses on your network are accepted.
        </p>
        {draft && (
          <div className="space-y-2 rounded-md bg-slate-950 p-3 ring-1 ring-slate-800">
            <div className="flex flex-wrap gap-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-400">Name</span>
                <input aria-label="Server name" className={`${input} w-44`} maxLength={40} value={draft.name}
                  placeholder="TrueNAS" onChange={(e) => set('name', e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-400">Address</span>
                <input aria-label="Server address" className={`${input} w-44`} value={draft.host} list="server-hosts"
                  placeholder="192.168.2.x" onChange={(e) => prefill(e.target.value)} />
                <datalist id="server-hosts">
                  {suggestions.filter((s) => !known.has(s.host) || s.host === draft.host).map((s) => (
                    <option key={s.host} value={s.host}>{s.label}</option>
                  ))}
                </datalist>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-400">Port</span>
                <input aria-label="Server port" className={`${input} w-20`} inputMode="numeric" value={draft.port}
                  onChange={(e) => set('port', e.target.value.replace(/\D/g, ''))} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-slate-400">User</span>
                <input aria-label="Server user" className={`${input} w-36`} maxLength={32} autoCapitalize="none"
                  autoCorrect="off" value={draft.username} placeholder="root" onChange={(e) => set('username', e.target.value)} />
              </label>
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-slate-400">Notes (optional)</span>
              <input aria-label="Server notes" className={`${input} w-full`} maxLength={200} value={draft.notes}
                onChange={(e) => set('notes', e.target.value)} />
            </label>
            <div className="flex gap-2">
              <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}
                disabled={busy || !draft.name.trim() || !draft.host.trim() || !draft.username.trim() || !draft.port}
                onClick={() => void save()}>
                {draft.id === null ? 'Add server' : 'Save'}
              </button>
              <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={() => { setDraft(null); setError(null); }}>
                Cancel
              </button>
            </div>
          </div>
        )}
        {servers && !servers.length && !draft && <p className="text-slate-500">No servers saved yet.</p>}
        {servers && servers.length > 0 && (
          <ul className="divide-y divide-slate-800" aria-label="Saved servers">
            {servers.map((s) => (
              <li key={s.id} className="space-y-1.5 py-2.5">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-slate-100">{s.name}</span>
                  <span className="break-all font-mono text-xs text-slate-400">{s.username}@{s.host}{s.port !== 22 ? `:${s.port}` : ''}</span>
                </div>
                {s.notes && <p className="text-xs text-slate-500">{s.notes}</p>}
                <div className="flex flex-wrap gap-2">
                  <a href={s.ssh_url} className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>Open in SSH app</a>
                  <button className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`}
                    onClick={() => void copy(s)}>
                    Copy command
                  </button>
                  <button className={`${btn} text-sky-300 ring-1 ring-slate-700 hover:bg-slate-800`}
                    onClick={() => setDraft({ id: s.id, name: s.name, host: s.host, port: String(s.port), username: s.username, notes: s.notes ?? '' })}>
                    Edit
                  </button>
                  <button className={`${btn} text-rose-300 ring-1 ring-slate-700 hover:bg-slate-800`}
                    onClick={() => setAsk({
                      title: `Remove ${s.name}?`,
                      body: <p>It is removed from this list only. Nothing changes on the server.</p>,
                      confirmLabel: 'Remove',
                      danger: true,
                      run: async () => {
                        await api.deleteServer(s.id);
                        await load();
                      },
                    })}>
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {note && <p className="break-all font-mono text-xs text-emerald-300" role="status">{note}</p>}
        {error && <p className="text-rose-400" role="alert">{error}</p>}
      </div>
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </section>
  );
}
