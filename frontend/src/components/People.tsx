import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type DeviceRow, type PeopleList, type Person } from '../lib/api';
import { GROUP_COLOURS, colourOf } from '../lib/groups';
import { usePolling } from '../lib/usePolling';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const input = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const MAX_DEVICES = 8;
const AWAY_CHOICES = [5, 10, 15, 20, 30, 45, 60, 90, 120];

/** "17:42" today, otherwise "Mon 17:42". */
export function clock(ms: number, now = Date.now()): string {
  const d = new Date(ms);
  const time = d.toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false });
  if (new Date(now).toDateString() === d.toDateString()) return time;
  return `${d.toLocaleDateString('en-CA', { weekday: 'short' })} ${time}`;
}

export function presenceText(p: Person, now = Date.now()): string {
  if (p.home === null) return 'no devices yet';
  const since = p.since_ms ? ` since ${clock(p.since_ms, now)}` : '';
  return p.home ? `home${since}` : `away${since}`;
}

/** Overview: who is home now. Shows nothing until someone is added (Devices → People…). */
export function HomeNow() {
  const { data } = usePolling(api.people, 60_000);
  if (!Array.isArray(data?.people) || !data.people.length) return null;
  return (
    <section aria-label="Who's home" className="rounded-lg bg-slate-900 p-3 ring-1 ring-slate-800">
      <h2 className="pb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Who's home</h2>
      <ul className="flex flex-wrap gap-2">
        {data.people.map((p) => {
          const c = colourOf(p.colour);
          return (
            <li key={p.id}>
              <Link to={`/people/${p.id}`}
                className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm ring-1 ${p.home ? c.chip : 'bg-slate-950 text-slate-400 ring-slate-800'}`}>
                <span className={`h-2 w-2 rounded-full ${p.home ? c.dot : 'bg-slate-600'}`} aria-hidden />
                <span className="font-medium">{p.name}</span>
                <span className="text-xs opacity-80">{presenceText(p)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ColourPicker({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Colour">
      {Object.keys(GROUP_COLOURS).map((c) => (
        <button key={c} type="button" role="radio" aria-checked={c === value} aria-label={c} onClick={() => onChange(c)}
          className={`h-5 w-5 rounded-full ${GROUP_COLOURS[c]!.dot} ${c === value ? 'ring-2 ring-white ring-offset-1 ring-offset-slate-900' : ''}`} />
      ))}
    </div>
  );
}

/** Pick up to 8 devices: search by name, IP or vendor; phones first. */
function DevicePicker({ devices, value, onChange }: { devices: DeviceRow[]; value: number[]; onChange: (ids: number[]) => void }) {
  const [q, setQ] = useState('');
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const rank = (d: DeviceRow) => (value.includes(d.id) ? 0 : d.type === 'phone' ? 1 : ['tablet', 'laptop'].includes(d.type) ? 2 : 3);
    return devices
      .filter((d) => !needle || [d.label, d.last_ip, d.vendor, d.hostname, d.mac].some((x) => x?.toLowerCase().includes(needle)))
      .sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label))
      .slice(0, 40);
  }, [devices, q, value]);
  const toggle = (id: number) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  return (
    <div className="space-y-1.5">
      <input aria-label="Find a device" placeholder="Find a device (name, IP, vendor)" className={`${input} w-full`} value={q} onChange={(e) => setQ(e.target.value)} />
      <ul className="max-h-48 divide-y divide-slate-800 overflow-y-auto rounded-md ring-1 ring-slate-800">
        {shown.map((d) => {
          const on = value.includes(d.id);
          return (
            <li key={d.id}>
              <label className="flex items-center gap-2 px-2 py-1.5 text-sm text-slate-200">
                <input type="checkbox" checked={on} disabled={!on && value.length >= MAX_DEVICES} onChange={() => toggle(d.id)} />
                <span className={`h-2 w-2 shrink-0 rounded-full ${d.online ? 'bg-emerald-400' : 'bg-slate-600'}`} aria-hidden />
                <span className="min-w-0 flex-1 truncate">{d.label}</span>
                <span className="shrink-0 text-xs text-slate-500">{d.type.replace('_', ' ')}</span>
              </label>
            </li>
          );
        })}
        {!shown.length && <li className="px-2 py-1.5 text-sm text-slate-500">No matching devices.</li>}
      </ul>
      <p className="text-xs text-slate-500">{value.length} of {MAX_DEVICES} chosen. Usually their phone; add a watch or laptop if they carry it.</p>
    </div>
  );
}

function Editor({ person, devices, onSaved, onCancel }: { person: Person | null; devices: DeviceRow[]; onSaved: () => Promise<void>; onCancel?: () => void }) {
  const [name, setName] = useState(person?.name ?? '');
  const [colour, setColour] = useState(person?.colour ?? 'sky');
  const [ids, setIds] = useState<number[]>(person?.device_ids ?? []);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setErr(null);
    try {
      if (person) await api.editPerson(person.id, { name: name.trim(), colour, device_ids: ids });
      else await api.createPerson({ name: name.trim(), colour, device_ids: ids });
      if (!person) {
        setName('');
        setIds([]);
      }
      await onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="space-y-2 rounded-md bg-slate-950 p-2 ring-1 ring-slate-800">
      <input aria-label={person ? 'Name' : 'New person name'} placeholder="Name, e.g. Sam" className={`${input} w-full`} maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
      <ColourPicker value={colour} onChange={setColour} />
      <DevicePicker devices={devices} value={ids} onChange={setIds} />
      {err && <p className="text-xs text-rose-300" role="alert">{err}</p>}
      <div className="flex gap-2">
        <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!name.trim()} onClick={() => void save()}>{person ? 'Save' : 'Add person'}</button>
        {onCancel && <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

/** Add, edit and remove people, and set how long before someone counts as away. */
export function PeopleManager({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<PeopleList | null>(null);
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [editing, setEditing] = useState<number | null>(null);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const reload = async () => setData(await api.people());
  useEffect(() => {
    void reload().catch((e) => setErr(String(e)));
    void api.devices().then((r) => setDevices(r.devices.filter((d) => d.merged_into === null))).catch(() => undefined);
  }, []);
  const away = data?.settings.away_after_min ?? 15;
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-slate-950/70 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Who's home" className="mt-10 w-full max-w-lg space-y-3 rounded-lg bg-slate-900 p-5 ring-1 ring-slate-700">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-white">Who's home</h2>
          <button onClick={onClose} className="text-sm text-slate-400 hover:text-white" aria-label="Close">✕</button>
        </div>
        <p className="text-xs text-slate-400">
          Someone is home while one of their devices is on the network. Arrivals and departures go on the timeline as information. Nothing leaves your network.
        </p>
        {data?.people.length ? (
          <ul className="divide-y divide-slate-800">
            {data.people.map((p) => (
              <li key={p.id} className="py-2">
                {editing === p.id ? (
                  <Editor person={p} devices={devices} onCancel={() => setEditing(null)} onSaved={async () => { setEditing(null); await reload(); }} />
                ) : (
                  <div className="flex items-center gap-2">
                    <span className={`h-3 w-3 rounded-full ${colourOf(p.colour).dot}`} aria-hidden />
                    <Link to={`/people/${p.id}`} onClick={onClose} className="flex-1 text-sm text-slate-100 hover:underline">{p.name}</Link>
                    <span className="text-xs text-slate-500">{p.device_ids.length} device{p.device_ids.length === 1 ? '' : 's'} · {presenceText(p)}</span>
                    <button className="text-xs text-sky-400 hover:underline" onClick={() => setEditing(p.id)}>Edit</button>
                    <button className="text-xs text-rose-300 hover:underline" onClick={() => setAsk({
                      title: `Remove ${p.name}?`,
                      body: <p>Their arrivals and departures are deleted from the timeline too. Their devices stay as they are.</p>,
                      confirmLabel: 'Remove',
                      danger: true,
                      run: async () => {
                        await api.deletePerson(p.id);
                        await reload();
                      },
                    })}>Remove</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          data && <p className="text-sm text-slate-400">Nobody yet. Add a person and pick their phone.</p>
        )}
        <div className="space-y-2 border-t border-slate-800 pt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Add a person</h3>
          <Editor person={null} devices={devices} onSaved={reload} />
        </div>
        <label className="flex flex-wrap items-center gap-2 border-t border-slate-800 pt-3 text-sm text-slate-300">
          Away after
          <select aria-label="Away after" className={input} value={away} onChange={async (e) => {
            setErr(null);
            try {
              await api.savePeopleSettings(Number(e.target.value));
              await reload();
            } catch (x) {
              setErr(x instanceof Error ? x.message : String(x));
            }
          }}>
            {AWAY_CHOICES.map((m) => <option key={m} value={m}>{m} minutes</option>)}
          </select>
          <span className="text-xs text-slate-500">without being seen. Phones sleep their Wi-Fi, so a short gap is not leaving.</span>
        </label>
        {err && <p className="text-sm text-rose-300" role="alert">{err}</p>}
      </div>
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </div>
  );
}
