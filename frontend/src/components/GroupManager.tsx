import { useRef, useState } from 'react';
import { api, type DeviceGroup } from '../lib/api';
import { GROUP_COLOURS, GroupIcon, ICON_NAMES, colourOf } from '../lib/groups';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const input = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';

function Picker({ colour, icon, onColour, onIcon }: { colour: string; icon: string; onColour: (c: string) => void; onIcon: (i: string) => void }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Colour">
        {Object.keys(GROUP_COLOURS).map((c) => (
          <button key={c} type="button" role="radio" aria-checked={c === colour} aria-label={c} onClick={() => onColour(c)}
            className={`h-5 w-5 rounded-full ${GROUP_COLOURS[c]!.dot} ${c === colour ? 'ring-2 ring-white ring-offset-1 ring-offset-slate-900' : ''}`} />
        ))}
      </div>
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Icon">
        {ICON_NAMES.map((i) => (
          <button key={i} type="button" role="radio" aria-checked={i === icon} aria-label={i} onClick={() => onIcon(i)}
            className={`rounded p-1 ${i === icon ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'}`}>
            <GroupIcon icon={i} className="h-4 w-4" />
          </button>
        ))}
      </div>
    </div>
  );
}

const QUIET_HELP = "Its devices' alerts (address changes, rejoining, unusual DNS) are recorded as information and hidden from the timeline. Outages still alert.";

function QuietBox({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-2 text-xs text-slate-300">
      <input type="checkbox" className="mt-0.5" checked={value} onChange={(e) => onChange(e.target.checked)} aria-label="Quiet" />
      <span><span className="font-medium text-slate-200">Quiet</span>: {QUIET_HELP}</span>
    </label>
  );
}

function Row({ g, onChanged, ask }: { g: DeviceGroup; onChanged: () => Promise<void>; ask: (c: Confirm) => void }) {
  const [edit, setEdit] = useState(false);
  const [quiet, setQuiet] = useState(g.quiet);
  const [name, setName] = useState(g.name);
  const [colour, setColour] = useState(g.colour);
  const [icon, setIcon] = useState(g.icon);
  const [err, setErr] = useState<string | null>(null);
  const c = colourOf(g.colour);
  const save = async () => {
    setErr(null);
    try {
      await api.editGroup(g.id, { name, colour, icon, quiet });
      setEdit(false);
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <li className="py-2">
      {!edit ? (
        <div className="flex items-center gap-2">
          <span className={`flex h-6 w-6 items-center justify-center rounded ${c.chip} ring-1`}><GroupIcon icon={g.icon} /></span>
          <span className="flex-1 text-sm text-slate-100">
            {g.name}
            {g.quiet && <span className="ml-2 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400" title={QUIET_HELP}>quiet</span>}
          </span>
          <span className="text-xs text-slate-500">{g.devices} device{g.devices === 1 ? '' : 's'}</span>
          <button className="text-xs text-sky-400 hover:underline" onClick={() => setEdit(true)}>Edit</button>
          <button className="text-xs text-rose-300 hover:underline" onClick={() => ask({
            title: `Delete the group ${g.name}?`,
            body: <p>{g.devices ? `Its ${g.devices} device${g.devices === 1 ? '' : 's'} will be ungrouped. ` : ''}The devices themselves, their names and tags are kept.</p>,
            confirmLabel: 'Delete group',
            danger: true,
            run: async () => {
              await api.deleteGroup(g.id);
              await onChanged();
            },
          })}>Delete</button>
        </div>
      ) : (
        <div className="space-y-2 rounded-md bg-slate-950 p-2 ring-1 ring-slate-800">
          <input aria-label="Group name" className={`${input} w-full`} maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
          <Picker colour={colour} icon={icon} onColour={setColour} onIcon={setIcon} />
          <QuietBox value={quiet} onChange={setQuiet} />
          {err && <p className="text-xs text-rose-300">{err}</p>}
          <div className="flex gap-2">
            <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!name.trim()} onClick={() => void save()}>Save</button>
            <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={() => setEdit(false)}>Cancel</button>
          </div>
        </div>
      )}
    </li>
  );
}

/** Create, edit and delete groups; export and import the whole set-up. */
export function GroupManager({ groups, onChanged, onClose }: { groups: DeviceGroup[]; onChanged: () => Promise<void>; onClose: () => void }) {
  const [name, setName] = useState('');
  const [colour, setColour] = useState('sky');
  const [icon, setIcon] = useState('star');
  const [quiet, setQuiet] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const create = async () => {
    setErr(null);
    try {
      await api.createGroup({ name: name.trim(), colour, icon, quiet });
      setName('');
      setQuiet(false);
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  const importFile = async (f: File) => {
    setErr(null);
    setNote(null);
    try {
      const r = await api.importGroups(JSON.parse(await f.text()));
      setNote(`Imported: ${r.groups_created} new groups, ${r.devices} devices` + (r.unknown_macs.length ? `; ${r.unknown_macs.length} devices not seen on this network yet` : '') + '.');
      await onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-slate-950/70 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label="Manage groups" className="mt-10 w-full max-w-lg space-y-3 rounded-lg bg-slate-900 p-5 ring-1 ring-slate-700">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-white">Groups</h2>
          <button onClick={onClose} className="text-sm text-slate-400 hover:text-white" aria-label="Close">✕</button>
        </div>
        {groups.length ? (
          <ul className="divide-y divide-slate-800">{groups.map((g) => <Row key={g.id} g={g} onChanged={onChanged} ask={setAsk} />)}</ul>
        ) : (
          <p className="text-sm text-slate-400">No groups yet. Create one below, or accept a suggestion on the Devices page.</p>
        )}
        <div className="space-y-2 border-t border-slate-800 pt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">New group</h3>
          <input aria-label="New group name" placeholder="e.g. Kids, Smart home, Sam's devices" className={`${input} w-full`} maxLength={40}
            value={name} onChange={(e) => setName(e.target.value)} />
          <Picker colour={colour} icon={icon} onColour={setColour} onIcon={setIcon} />
          <QuietBox value={quiet} onChange={setQuiet} />
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!name.trim()} onClick={() => void create()}>Create group</button>
        </div>
        {err && <p className="text-sm text-rose-300" role="alert">{err}</p>}
        {note && <p className="text-sm text-emerald-300">{note}</p>}
        <div className="flex flex-wrap gap-3 border-t border-slate-800 pt-3 text-xs">
          <a href={api.groupsExportUrl} className="text-sky-400 hover:underline">Export groups and tags</a>
          <button className="text-sky-400 hover:underline" onClick={() => file.current?.click()}>Import…</button>
          <input ref={file} type="file" accept="application/json,.json" className="hidden" aria-label="Import file"
            onChange={(e) => e.target.files?.[0] && void importFile(e.target.files[0])} />
          <span className="text-slate-500">Devices are matched by hardware address, so an export survives a fresh install.</span>
        </div>
      </div>
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </div>
  );
}
