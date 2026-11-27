import { useEffect, useState } from 'react';
import { api, type AssetsState } from '../lib/api';

const input =
  'w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none sm:w-auto';

/** On a device's page: which Deco or switch it is plugged into. Automatic follows the Deco API (or
 * the Hub); pinning overrides it on the map only. A Wi-Fi device cannot be put behind a switch. */
export function ConnectedThrough({ id, wifi }: { id: number; wifi: boolean }) {
  const [state, setState] = useState<AssetsState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.assets().then((s) => setState(Array.isArray(s?.assets) ? s : null), () => undefined);
  }, [id]);

  if (!state || (state.assets.length === 0 && state.decos.length === 0)) return null;
  const value = state.pins[`dev:${id}`] ?? '';

  async function change(next: string) {
    setBusy(true);
    setErr(null);
    try {
      setState(await api.assignAssets([`dev:${id}`], next || null));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-2 rounded-lg bg-slate-900 p-4 text-sm ring-1 ring-slate-800" aria-label="Connected through">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Connected through</h2>
      <p className="text-xs text-slate-500">
        Where this device sits on the network map. Automatic uses what the Decos report, else the Home Hub.
      </p>
      <select aria-label="Plugged into" className={input} value={value} disabled={busy} onChange={(e) => void change(e.target.value)}>
        <option value="">Automatic</option>
        {state.decos.length > 0 && (
          <optgroup label="Decos">
            {state.decos.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </optgroup>
        )}
        {state.assets.length > 0 && (
          <optgroup label="Switches">
            {state.assets.map((a) => (
              <option key={a.id} value={`asset:${a.id}`} disabled={wifi}>{a.name}{wifi ? ' (not for a Wi-Fi device)' : ''}</option>
            ))}
          </optgroup>
        )}
      </select>
      {err && <p className="text-xs text-rose-300" role="alert">{err}</p>}
    </section>
  );
}
