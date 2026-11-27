import { useEffect, useState } from 'react';
import { api, type AssetsState } from '../lib/api';

const input =
  'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';

/** "Add switch" on the map, and where NetWatch thinks one is plugged in (a Deco with several wired
 * devices). Suggestions only: a switch is created, and devices pinned, when you press a button. */
export function SwitchTools({ onChanged, refreshKey }: { onChanged: () => Promise<void>; refreshKey?: unknown }) {
  const [state, setState] = useState<AssetsState | null>(null);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [uplink, setUplink] = useState('hub');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.assets().then((s) => setState(Array.isArray(s?.assets) ? s : null), () => undefined);
  }, [refreshKey]);

  async function run(act: () => Promise<unknown>) {
    setBusy(true);
    setErr(null);
    try {
      await act();
      const s = await api.assets();
      setState(Array.isArray(s?.assets) ? s : null);
      await onChanged();
      return true;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!state) return null;
  const hints = state.hints.decos;
  return (
    <section className="space-y-2" aria-label="Switches">
      <div className="flex flex-wrap items-center gap-2">
        <button className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`} aria-expanded={open}
          onClick={() => setOpen((o) => !o)}>
          Add switch
        </button>
        {open && (
          <>
            <input aria-label="Switch name" className={input} placeholder="Name, e.g. Office switch" value={name} maxLength={40}
              onChange={(e) => setName(e.target.value)} />
            <select aria-label="New switch plugs into" className={input} value={uplink} onChange={(e) => setUplink(e.target.value)}>
              <option value="hub">Plugs into the Home Hub</option>
              {state.decos.map((d) => <option key={d.id} value={d.id}>Plugs into {d.name}</option>)}
              {state.assets.map((a) => <option key={a.id} value={`asset:${a.id}`}>Plugs into {a.name}</option>)}
            </select>
            <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy || !name.trim()}
              onClick={() => void run(async () => {
                await api.addAsset({ name: name.trim(), uplink });
                setName('');
                setOpen(false);
              })}>
              Add
            </button>
          </>
        )}
      </div>
      {hints.map((h) => (
        <p key={h.deco} className="rounded-md bg-sky-950/30 p-2 text-xs text-sky-100 ring-1 ring-sky-900">
          {h.name} has {h.devices.length} wired devices ({h.devices.slice(0, 3).map((d) => d.label).join(', ')}
          {h.devices.length > 3 ? ' and more' : ''}). That is more than a Deco usually has ports, so a switch is probably plugged into it.{' '}
          <button className="font-medium text-sky-300 underline" disabled={busy}
            onClick={() => void run(async () => {
              const made = await api.addAsset({ name: `${h.name} switch`.slice(0, 40), uplink: h.deco });
              await api.assignAssets(h.devices.map((d) => `dev:${d.id}`), `asset:${made.asset!.id}`);
            })}>
            Create a switch on {h.name} with these devices
          </button>
        </p>
      ))}
      {err && <p className="text-xs text-rose-300" role="alert">{err}</p>}
    </section>
  );
}
