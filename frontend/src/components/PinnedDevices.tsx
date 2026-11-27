import { useEffect, useMemo, useState } from 'react';
import { api, type AssetsState, type DeviceRow } from '../lib/api';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';

export type PinCandidate = { key: string; label: string; likely: boolean; wifi: boolean };

/** Devices that can still be pinned: not merged, not pinned anywhere yet. The ones no Deco claimed
 * come first, then cabled ones; a device last reported on Wi-Fi is listed last (the report can be
 * stale, for example after a network card swap), so nothing is ever hidden. */
export function pinCandidates(devices: DeviceRow[], state: AssetsState): PinCandidate[] {
  const likely = new Set(state.hints.unclaimed.map((d) => d.id));
  const list = devices
    .filter((d) => d.merged_into == null && !state.pins[`dev:${d.id}`])
    .map((d) => {
      const c = (d.details as { connection?: string } | undefined)?.connection;
      return { key: `dev:${d.id}`, label: d.label, likely: likely.has(d.id), wifi: !!c && c !== 'wired' };
    });
  if (!state.pins.truenas) list.unshift({ key: 'truenas', label: 'TrueNAS (NetWatch)', likely: false, wifi: false });
  return list.sort(
    (a, b) => Number(b.likely) - Number(a.likely) || Number(a.wifi) - Number(b.wifi) || a.label.localeCompare(b.label),
  );
}

/** "Behind this": what is pinned here, and a tick list to pin more. Shared by switches, Decos and the Hub. */
export function PinnedDevices({
  members,
  candidates,
  busy,
  heading = 'Behind this switch',
  onAssign,
}: {
  members: { key: string; label: string }[];
  candidates: PinCandidate[];
  busy: boolean;
  heading?: string;
  onAssign: (keys: string[], pin: boolean) => Promise<void>;
}) {
  const [pick, setPick] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setPick((cur) => {
      const n = new Set(cur);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  return (
    <>
      <div>
        <h3 className="mb-1 text-xs uppercase tracking-wide text-slate-500">{heading}</h3>
        {members.length === 0 ? (
          <p className="text-xs text-slate-500">Nothing pinned yet.</p>
        ) : (
          <ul className="space-y-1">
            {members.map((m) => (
              <li key={m.key} className="flex items-center justify-between gap-2 text-sm text-slate-200">
                <span className="truncate">{m.label}</span>
                <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} disabled={busy}
                  aria-label={`Unpin ${m.label}`} onClick={() => void onAssign([m.key], false)}>
                  Unpin
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <details>
        <summary className="cursor-pointer text-xs text-slate-400">Pin devices here</summary>
        <div className="mt-2 space-y-1">
          {candidates.length === 0 && <p className="text-xs text-slate-500">Every device is already pinned somewhere.</p>}
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {candidates.map((c, i) => (
              <div key={c.key}>
                {c.likely && (i === 0 || !candidates[i - 1]!.likely) && <p className="text-[11px] text-sky-300">Probably here (no Deco reported them)</p>}
                {!c.likely && i > 0 && candidates[i - 1]!.likely && <p className="mt-1 text-[11px] text-slate-500">Others</p>}
                <label className="flex items-center gap-2 text-sm text-slate-200">
                  <input type="checkbox" checked={pick.has(c.key)} onChange={() => toggle(c.key)} />
                  <span>{c.label}{c.wifi ? <span className="text-slate-500"> (reported on Wi-Fi)</span> : null}</span>
                </label>
              </div>
            ))}
          </div>
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy || pick.size === 0}
            onClick={() => void onAssign([...pick], true).then(() => setPick(new Set()))}>
            Pin {pick.size || ''} {pick.size === 1 ? 'device' : 'devices'}
          </button>
        </div>
      </details>
    </>
  );
}

/** The Home Hub's or a Deco's own ports: pin devices behind it, or take them off again. */
export function NodePins({ parent, onChanged }: { parent: string; onChanged: () => Promise<void> }) {
  const [state, setState] = useState<AssetsState | null>(null);
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setErr(null);
    api.assets().then((s) => setState(Array.isArray(s?.assets) && s.pins && s.hints ? s : null), () => undefined);
    api.devices().then((r) => setDevices(Array.isArray(r?.devices) ? r.devices : []), () => undefined);
  }, [parent]);

  const labels = useMemo(() => new Map(devices.map((d) => [`dev:${d.id}`, d.label])), [devices]);
  const members = useMemo(
    () =>
      Object.entries(state?.pins ?? {})
        .filter(([, p]) => p === parent)
        .map(([key]) => ({ key, label: key === 'truenas' ? 'TrueNAS (NetWatch)' : (labels.get(key) ?? 'A removed device') }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [state, parent, labels],
  );
  const candidates = useMemo(() => (state ? pinCandidates(devices, state) : []), [devices, state]);
  if (!state) return null;

  async function assign(keys: string[], pin: boolean) {
    setBusy(true);
    setErr(null);
    try {
      const s = await api.assignAssets(keys, pin ? parent : null);
      setState(s);
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 border-t border-slate-800 pt-3" aria-label="Pinned devices">
      <PinnedDevices heading="Pinned behind this" members={members} candidates={candidates} busy={busy} onAssign={assign} />
      {err && <p className="text-xs text-rose-300" role="alert">{err}</p>}
    </section>
  );
}
