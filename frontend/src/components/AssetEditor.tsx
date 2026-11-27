import { useEffect, useMemo, useState } from 'react';
import { api, type AssetsState, type DeviceRow } from '../lib/api';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const input =
  'w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';

/** What a switch plugs into, as a label. */
export function uplinkLabel(state: AssetsState, uplink: string): string {
  if (uplink === 'hub') return 'The Home Hub';
  if (uplink.startsWith('deco:')) return state.decos.find((d) => d.id === uplink)?.name ?? 'A Deco';
  return state.assets.find((a) => `asset:${a.id}` === uplink)?.name ?? 'Another switch';
}

/** A switch's settings in the map's Details panel: name, what it plugs into, what is pinned behind it. */
export function AssetEditor({ id, onChanged }: { id: number; onChanged: () => Promise<void> }) {
  const [state, setState] = useState<AssetsState | null>(null);
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [name, setName] = useState('');
  const [uplink, setUplink] = useState('hub');
  const [notes, setNotes] = useState('');
  const [pick, setPick] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState<Confirm | null>(null);

  const asset = state?.assets.find((a) => a.id === id) ?? null;
  const load = async () => {
    const s = await api.assets();
    if (!Array.isArray(s?.assets)) throw new Error('The switch list could not be read.');
    setState(s);
    const a = s.assets.find((x) => x.id === id);
    if (a) {
      setName(a.name);
      setUplink(a.uplink);
      setNotes(a.notes ?? '');
    }
  };
  useEffect(() => {
    setPick(new Set());
    setErr(null);
    load().catch((e) => setErr(e instanceof Error ? e.message : String(e)));
    api.devices().then((r) => setDevices(r.devices), () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function act(run: () => Promise<AssetsState | void>) {
    setBusy(true);
    setErr(null);
    try {
      const s = await run();
      if (s) setState(s);
      await load();
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Devices that can still be pinned here: not already pinned, not on Wi-Fi (a Wi-Fi device
  // cannot sit behind a cable), with the ones no Deco claims first.
  const likely = useMemo(() => new Set((state?.hints.unclaimed ?? []).map((d) => d.id)), [state]);
  const candidates = useMemo(() => {
    if (!state) return [];
    const list = devices
      .filter((d) => d.merged_into == null && !state.pins[`dev:${d.id}`])
      .filter((d) => {
        const c = (d.details as { connection?: string } | undefined)?.connection;
        return !c || c === 'wired';
      })
      .map((d) => ({ key: `dev:${d.id}`, label: d.label, likely: likely.has(d.id) }));
    if (!state.pins.truenas) list.unshift({ key: 'truenas', label: 'TrueNAS (NetWatch)', likely: false });
    return list.sort((a, b) => Number(b.likely) - Number(a.likely) || a.label.localeCompare(b.label));
  }, [devices, state, likely]);

  if (!state) return err ? <p className="text-xs text-rose-300" role="alert">{err}</p> : <p className="text-xs text-slate-500">Loading…</p>;
  if (!asset) return null;
  const changed = name.trim() !== asset.name || uplink !== asset.uplink || notes.trim() !== (asset.notes ?? '');
  const others = state.assets.filter((a) => a.id !== id);
  const toggle = (k: string) =>
    setPick((cur) => {
      const n = new Set(cur);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  return (
    <section className="space-y-3 border-t border-slate-800 pt-3" aria-label="Switch settings">
      <p className="text-xs text-slate-500">
        An unmanaged switch cannot be seen on the network, so this is drawn by hand. It is up when something behind it is.
      </p>
      <label className="block space-y-1 text-xs text-slate-400">
        Name
        <input className={input} value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="block space-y-1 text-xs text-slate-400">
        Plugs into
        <select aria-label="Plugs into" className={input} value={uplink} onChange={(e) => setUplink(e.target.value)}>
          <option value="hub">The Home Hub</option>
          {state.decos.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          {others.map((o) => <option key={o.id} value={`asset:${o.id}`}>{o.name}</option>)}
        </select>
      </label>
      <label className="block space-y-1 text-xs text-slate-400">
        Notes
        <input className={input} value={notes} maxLength={200} onChange={(e) => setNotes(e.target.value)} placeholder="Where it is, how many ports" />
      </label>
      <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy || !changed || !name.trim()}
        onClick={() => void act(() => api.saveAsset(id, { name: name.trim(), uplink, notes: notes.trim() }))}>
        Save
      </button>

      <div>
        <h3 className="mb-1 text-xs uppercase tracking-wide text-slate-500">Behind this switch</h3>
        {asset.members.length === 0 ? (
          <p className="text-xs text-slate-500">Nothing pinned yet.</p>
        ) : (
          <ul className="space-y-1">
            {asset.members.map((m) => (
              <li key={m.key} className="flex items-center justify-between gap-2 text-sm text-slate-200">
                <span className="truncate">{m.label}</span>
                <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} disabled={busy}
                  aria-label={`Unpin ${m.label}`} onClick={() => void act(() => api.assignAssets([m.key], null))}>
                  Unpin
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <details>
        <summary className="cursor-pointer text-xs text-slate-400">Pin devices behind this switch</summary>
        <div className="mt-2 space-y-1">
          {candidates.length === 0 && <p className="text-xs text-slate-500">Every wired device is already pinned somewhere.</p>}
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {candidates.map((c, i) => (
              <div key={c.key}>
                {c.likely && (i === 0 || !candidates[i - 1]!.likely) && <p className="text-[11px] text-sky-300">Probably here (no Deco reported them)</p>}
                {!c.likely && i > 0 && candidates[i - 1]!.likely && <p className="mt-1 text-[11px] text-slate-500">Others</p>}
                <label className="flex items-center gap-2 text-sm text-slate-200">
                  <input type="checkbox" checked={pick.has(c.key)} onChange={() => toggle(c.key)} /> {c.label}
                </label>
              </div>
            ))}
          </div>
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy || pick.size === 0}
            onClick={() => void act(async () => { const s = await api.assignAssets([...pick], `asset:${id}`); setPick(new Set()); return s; })}>
            Pin {pick.size || ''} {pick.size === 1 ? 'device' : 'devices'}
          </button>
        </div>
      </details>

      {err && <p className="text-xs text-rose-300" role="alert">{err}</p>}
      <button className={`${btn} text-rose-300 ring-1 ring-slate-700 hover:bg-slate-800`} disabled={busy}
        onClick={() => setAsk({
          title: `Remove ${asset.name}?`,
          body: <p>What is pinned behind it, and any switch plugged into it, moves to {uplinkLabel(state, asset.uplink)}. Devices are not changed.</p>,
          confirmLabel: 'Remove switch',
          danger: true,
          run: async () => { await api.deleteAsset(id); await onChanged(); },
        })}>
        Remove switch
      </button>
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </section>
  );
}
