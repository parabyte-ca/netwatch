import { useEffect, useId, useState } from 'react';
import { api, type CopyDeviceResult, type CopyItem, type DeviceRow } from '../lib/api';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const ITEMS: { id: CopyItem; label: string }[] = [
  { id: 'allow', label: 'Always-allowed domains' },
  { id: 'apps', label: 'Blocked apps' },
  { id: 'filtering', label: 'Filtering and YouTube' },
  { id: 'schedules', label: 'Internet-off schedules' },
];

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const select = 'w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';

/** "Copy from another device": the source's own allow and block settings, copied onto this device
 * (and, optionally, more devices), merged into or replacing what they have. */
export function CopyDeviceSettings({ d, onDone }: { d: { id: number; label: string }; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [source, setSource] = useState('');
  const [items, setItems] = useState<Set<CopyItem>>(new Set(ITEMS.map((i) => i.id)));
  const [mode, setMode] = useState<'merge' | 'replace'>('merge');
  const [also, setAlso] = useState<Set<number>>(new Set());
  const [preview, setPreview] = useState<CopyDeviceResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const title = useId();

  useEffect(() => {
    if (!open) return;
    let live = true;
    api.devices().then(
      (r) => live && setDevices(r.devices.filter((x) => x.merged_into == null && x.id !== d.id).sort((a, b) => a.label.localeCompare(b.label))),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [open, d.id]);

  const body = () => ({ from_device_id: Number(source), also_to: [...also].filter((x) => x !== Number(source)), items: [...items], mode });
  useEffect(() => {
    setPreview(null);
    setError(null);
    if (!open || !source || items.size === 0) return;
    let live = true;
    api.copyDeviceSettings(d.id, { ...body(), preview: true }).then(
      (r) => live && setPreview(r),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
    // body() reads the same state this effect depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, source, items, mode, also, d.id]);

  const toggle = <T,>(set: Set<T>, v: T, put: (s: Set<T>) => void) => {
    const next = new Set(set);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    put(next);
  };
  const sourceLabel = devices.find((x) => String(x.id) === source)?.label ?? '';
  const names = preview ? preview.destinations.map((x) => x.label).join(', ') : '';

  function copy() {
    setAsk({
      title: `Copy ${sourceLabel}'s settings to ${names || d.label}?`,
      body: (
        <>
          <p>{mode === 'replace'
            ? 'Each device ends up with the same choices as the source for what you ticked. Its own choices for those are removed.'
            : 'The source\'s choices are added to what each device already has. Its own filtering and YouTube choices stay where it has one.'}</p>
          <ul className="list-disc space-y-0.5 pl-5">
            {preview?.destinations.map((x) => <li key={x.device_id}>{x.label}: {x.applied.join('; ') || 'nothing would change'}</li>)}
          </ul>
        </>
      ),
      confirmLabel: 'Copy settings',
      danger: mode === 'replace',
      run: async () => {
        await api.copyDeviceSettings(d.id, body());
        setOpen(false);
        await onDone();
      },
    });
  }

  if (!open) {
    return <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => setOpen(true)}>Copy from another device</button>;
  }
  const others = devices.filter((x) => String(x.id) !== source);
  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4" onMouseDown={(e) => e.target === e.currentTarget && setOpen(false)}>
        <div role="dialog" aria-modal="true" aria-labelledby={title} className="max-h-[90dvh] w-full max-w-md space-y-3 overflow-y-auto rounded-lg bg-slate-900 p-5 text-sm text-slate-300 shadow-xl ring-1 ring-slate-700">
          <h2 id={title} className="text-base font-semibold text-white">Copy settings to {d.label}</h2>
          <label className="block space-y-1">
            <span className="text-xs uppercase tracking-wide text-slate-400">Copy from</span>
            <select aria-label="Copy from" className={select} value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="">Choose a device…</option>
              {devices.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
            </select>
          </label>
          <fieldset className="space-y-1">
            <legend className="text-xs uppercase tracking-wide text-slate-400">What to copy</legend>
            {ITEMS.map((i) => (
              <label key={i.id} className="flex items-center gap-2">
                <input type="checkbox" checked={items.has(i.id)} onChange={() => toggle(items, i.id, setItems)} /> {i.label}
              </label>
            ))}
          </fieldset>
          <fieldset className="space-y-1">
            <legend className="text-xs uppercase tracking-wide text-slate-400">If {d.label} already has settings</legend>
            <label className="flex items-center gap-2"><input type="radio" name="copy-mode" checked={mode === 'merge'} onChange={() => setMode('merge')} /> Merge: add to what it has</label>
            <label className="flex items-center gap-2"><input type="radio" name="copy-mode" checked={mode === 'replace'} onChange={() => setMode('replace')} /> Replace: make it match the source</label>
          </fieldset>
          {others.length > 0 && (
            <details>
              <summary className="cursor-pointer text-xs text-slate-400">Also copy to other devices{also.size ? ` (${also.size})` : ''}</summary>
              <div className="mt-1 max-h-36 space-y-1 overflow-y-auto">
                {others.map((x) => (
                  <label key={x.id} className="flex items-center gap-2">
                    <input type="checkbox" checked={also.has(x.id)} onChange={() => toggle(also, x.id, setAlso)} /> {x.label}
                  </label>
                ))}
              </div>
            </details>
          )}
          {error && <p className="text-rose-300" role="alert">{error}</p>}
          {preview && (
            <div className="space-y-1 rounded-md bg-slate-800/60 p-2" aria-label="Preview">
              {preview.destinations.map((x) => (
                <p key={x.device_id}>
                  <span className="font-medium text-slate-100">{x.label}:</span> {x.applied.join('; ') || 'nothing would change'}
                  {x.skipped.map((s) => <span key={s} className="block text-xs text-amber-300">{s}</span>)}
                </p>
              ))}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <button className="rounded-md px-3 py-1.5 text-sm text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800" onClick={() => setOpen(false)}>Cancel</button>
            <button className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
              disabled={!source || items.size === 0 || !preview} onClick={copy}>Copy…</button>
          </div>
        </div>
      </div>
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </>
  );
}
