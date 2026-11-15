import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { GroupManager } from '../components/GroupManager';
import { GroupStrip } from '../components/GroupStrip';
import { api, type DeviceGroup, type DeviceRow, type DiscoveryStatus, type GroupSuggestion } from '../lib/api';
import { GroupBadge, GroupIcon, TagChip, colourOf } from '../lib/groups';
import { DEVICE_TYPE_LABEL, fmtAgo, fmtTime } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const input = 'rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';

function DiscoveryPanel({ status, refresh }: { status: DiscoveryStatus; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const p = status.passive;
  const a = status.active;
  const scan = a.last_scan;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await refresh();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setBusy(false);
    }
  }

  const learning = status.learning_until_ms && status.learning_until_ms > Date.now();
  return (
    <section className="grid gap-3 rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800 md:grid-cols-2" aria-label="Discovery">
      <div className="space-y-1 text-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Passive discovery</h2>
        <p>
          {p.running ? (
            <span className="text-emerald-300">Listening on {status.interface}</span>
          ) : !p.enabled ? (
            <span className="text-slate-400">Off (NETWATCH_DISCOVERY_PASSIVE=false)</span>
          ) : (
            <span className="text-rose-300">Not capturing{p.error ? `: ${p.error}` : ''}</span>
          )}
          {status.address && <span className="text-slate-500"> · {status.address}</span>}
        </p>
        <p className="text-xs text-slate-500">
          Reads ARP, DHCP, mDNS and SSDP broadcasts plus the host's ARP cache. Nothing is sent.
          {' '}Seen so far: {Object.entries(p.packets).map(([k, v]) => `${k} ${v}`).join(', ') || 'nothing yet'}.
        </p>
        {learning && (
          <p className="text-xs text-sky-300">
            Learning the network until {fmtTime(status.learning_until_ms!)}: "new device" alerts start after that.
          </p>
        )}
      </div>
      <div className="space-y-2 text-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Active scanning</h2>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={a.enabled}
            disabled={busy}
            onChange={(e) => void act(() => api.setDiscovery(e.target.checked))}
          />
          <span>Scan {a.networks.join(', ') || 'local subnet'} every {a.interval_min} min</span>
        </label>
        <p className="text-xs text-slate-500">
          ARP sweep at {a.rate_pps} packets/s, one DHCP server probe, SSDP and mDNS queries, and reverse DNS via the gateway.
          Private subnets only, never more than a /22.
        </p>
        {a.notes.map((n) => <p key={n} className="text-xs text-amber-300">{n}</p>)}
        <div className="flex flex-wrap items-center gap-3">
          <button
            className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}
            disabled={busy || a.scanning || !!a.unavailable}
            title={a.unavailable ?? undefined}
            onClick={() => void act(api.scanNow)}
          >
            {a.scanning || busy ? 'Scanning…' : 'Scan now'}
          </button>
          {scan && (
            <span className="text-xs text-slate-400">
              Last scan {fmtAgo(scan.finished_ms ?? scan.started_ms, Date.now())}
              {scan.hosts !== undefined && ` · ${scan.hosts} addresses`}
              {scan.error && <span className="text-rose-300"> · {scan.error}</span>}
              {scan.errors?.length ? <span className="text-rose-300"> · {scan.errors.join('; ')}</span> : null}
            </span>
          )}
        </div>
        {err && <p className="text-xs text-rose-400">{err}</p>}
      </div>
      {status.dhcp_servers.length > 0 && (
        <p className="text-xs text-slate-400 md:col-span-2">
          DHCP servers seen (24 h):{' '}
          {status.dhcp_servers.map((s) => `${s.ip}${s.vendor ? ` (${s.vendor})` : ''}`).join(', ')}
          {status.dhcp_servers.length > 1 && <span className="text-rose-300"> · more than one!</span>}
        </p>
      )}
    </section>
  );
}

type SortKey = 'device' | 'type' | 'ip' | 'mac' | 'seen';
type Sort = { key: SortKey; dir: 'asc' | 'desc' };
const SORT_STORE = 'netwatch.devices.sort';

/** IPv4 as a sortable number; devices without an address sort last. */
function ipValue(ip: string | null): number {
  if (!ip) return Number.POSITIVE_INFINITY;
  return ip.split('.').reduce((n, part) => n * 256 + (Number(part) || 0), 0);
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function compare(a: DeviceRow, b: DeviceRow, key: SortKey): number {
  switch (key) {
    case 'device':
      return collator.compare(a.label, b.label);
    case 'type':
      return collator.compare(DEVICE_TYPE_LABEL[a.type] ?? a.type, DEVICE_TYPE_LABEL[b.type] ?? b.type);
    case 'ip':
      return ipValue(a.last_ip) - ipValue(b.last_ip);
    case 'mac':
      return collator.compare(a.vendor ?? '~', b.vendor ?? '~') || collator.compare(a.macs[0] ?? '', b.macs[0] ?? '');
    case 'seen':
      return (a.online ? 1 : 0) - (b.online ? 1 : 0) || a.last_seen_ms - b.last_seen_ms;
  }
}

function loadSort(): Sort | null {
  try {
    const s = JSON.parse(localStorage.getItem(SORT_STORE) ?? 'null');
    return s && typeof s.key === 'string' && (s.dir === 'asc' || s.dir === 'desc') ? s : null;
  } catch {
    return null;
  }
}

function SortHeader({ k, label, sort, onSort, className = '', align = 'left' }: {
  k: SortKey; label: string; sort: Sort | null; onSort: (k: SortKey) => void; className?: string; align?: 'left' | 'right';
}) {
  const active = sort?.key === k;
  return (
    <th className={`px-3 py-2 ${className}`} aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button onClick={() => onSort(k)} className={`inline-flex items-center gap-1 uppercase tracking-wide hover:text-slate-200 ${active ? 'text-slate-200' : ''} ${align === 'right' ? 'flex-row-reverse' : ''}`}>
        {label}
        <span aria-hidden className="text-[10px]">{active ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}</span>
      </button>
    </th>
  );
}

/** "12 devices look like Smart home": review the list, untick, accept. Nothing is assigned until then. */
function Suggestions({ items, onDone }: { items: GroupSuggestion[]; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState<string | null>(null);
  const [off, setOff] = useState<Set<number>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const s = items.find((x) => x.name === open);
  const accept = async (x: GroupSuggestion) => {
    setErr(null);
    try {
      await api.acceptSuggestion(x.name, x.devices.filter((d) => !off.has(d.id)).map((d) => d.id));
      setOpen(null);
      setOff(new Set());
      await onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <section className="space-y-2 rounded-lg bg-sky-950/30 p-3 ring-1 ring-sky-900" aria-label="Group suggestions">
      <div className="flex flex-wrap items-center gap-2 text-sm text-sky-100">
        <span>Suggested groups:</span>
        {items.map((x) => (
          <button key={x.name} onClick={() => { setOpen(open === x.name ? null : x.name); setOff(new Set()); }}
            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs ring-1 ${colourOf(x.colour).chip}`}>
            <GroupIcon icon={x.icon} className="h-3 w-3" /> {x.devices.length} look like {x.name}
          </button>
        ))}
      </div>
      {s && (
        <div className="space-y-2 rounded-md bg-slate-900 p-3 ring-1 ring-slate-800">
          <p className="text-xs text-slate-400">Untick any device that does not belong in {s.name}{s.group_id ? '' : ' (the group is created when you accept)'}.</p>
          <ul className="grid gap-1 sm:grid-cols-2">
            {s.devices.map((d) => (
              <li key={d.id}>
                <label className="flex items-center gap-2 text-sm text-slate-200">
                  <input type="checkbox" checked={!off.has(d.id)}
                    onChange={() => setOff((cur) => { const n = new Set(cur); if (n.has(d.id)) n.delete(d.id); else n.add(d.id); return n; })} />
                  {d.label} <span className="text-xs text-slate-500">{DEVICE_TYPE_LABEL[d.type] ?? d.type}</span>
                </label>
              </li>
            ))}
          </ul>
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={s.devices.every((d) => off.has(d.id))} onClick={() => void accept(s)}>
            Add {s.devices.filter((d) => !off.has(d.id)).length} to {s.name}
          </button>
          {err && <p className="text-xs text-rose-300">{err}</p>}
        </div>
      )}
    </section>
  );
}

/** Bulk actions for the ticked devices: set group, add or remove a tag. */
function BulkBar({ ids, groups, onDone, onClear }: { ids: number[]; groups: DeviceGroup[]; onDone: () => Promise<void>; onClear: () => void }) {
  const [tag, setTag] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const run = async (body: { group_id?: number; add_tags?: string[]; remove_tags?: string[] }) => {
    setErr(null);
    try {
      await api.bulkDevices({ ids, ...body });
      await onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="sticky top-2 z-10 flex flex-wrap items-center gap-2 rounded-lg bg-slate-800 p-2 text-sm shadow-lg ring-1 ring-slate-700" role="toolbar" aria-label="Bulk actions">
      <span className="px-1 text-slate-200">{ids.length} selected</span>
      <select aria-label="Set group" className={input} value="" onChange={(e) => e.target.value && void run({ group_id: Number(e.target.value) })}>
        <option value="">Set group…</option>
        {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        <option value="0">No group</option>
      </select>
      <input aria-label="Tag" placeholder="tag" className={`${input} w-28`} value={tag} onChange={(e) => setTag(e.target.value)} />
      <button className={`${btn} bg-slate-700 text-slate-100 hover:bg-slate-600`} disabled={!tag.trim()} onClick={() => void run({ add_tags: [tag.trim()] })}>Add tag</button>
      <button className={`${btn} bg-slate-700 text-slate-100 hover:bg-slate-600`} disabled={!tag.trim()} onClick={() => void run({ remove_tags: [tag.trim()] })}>Remove tag</button>
      <button className="ml-auto px-2 text-xs text-slate-400 hover:text-white" onClick={onClear}>Clear selection</button>
      {err && <p className="w-full text-xs text-rose-300">{err}</p>}
    </div>
  );
}

function names(d: DeviceRow) {
  const extra = [d.hostname, d.mdns_name, d.model].filter((n) => n && n !== d.label);
  return extra.length ? extra.join(' · ') : null;
}

export function Devices() {
  const [q, setQ] = useState('');
  const [onlineOnly, setOnlineOnly] = useState(false);
  const [type, setType] = useState('');
  const [params, setParams] = useSearchParams();
  const groupFilter = params.get('group') ?? '';
  const tagFilter = params.get('tag') ?? '';
  const setFilter = (key: 'group' | 'tag', value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [managing, setManaging] = useState(false);
  const groupsQ = usePolling(api.deviceGroups, 60_000);
  const suggestQ = usePolling(api.groupSuggestions, 120_000);
  const [sort, setSort] = useState<Sort | null>(loadSort);
  const onSort = (key: SortKey) => {
    // First click: A→Z (or most recent first for "last seen"); second click reverses.
    const first = key === 'seen' ? 'desc' : 'asc';
    const next: Sort = sort?.key === key ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: first };
    setSort(next);
    try {
      localStorage.setItem(SORT_STORE, JSON.stringify(next));
    } catch {
      /* private mode: the order just isn't remembered */
    }
  };
  const status = usePolling(api.discovery, 15_000);
  const { data, error, refresh } = usePolling(() => api.devices(q.trim() || undefined), 15_000, [q]);
  const groups = groupsQ.data?.groups ?? [];
  const tags = [...new Set((data?.devices ?? []).flatMap((d) => d.tags ?? []))].sort();
  const reloadAll = async () => {
    await Promise.all([refresh(), groupsQ.refresh(), suggestQ.refresh()]);
  };

  const filtered = (data?.devices ?? []).filter((d) => (!onlineOnly || d.online) && (!type || d.type === type)
    && (!groupFilter || (groupFilter === '0' ? !d.group : String(d.group?.id) === groupFilter))
    && (!tagFilter || (d.tags ?? []).includes(tagFilter)));
  const rows = sort
    ? [...filtered].sort((a, b) => (sort.dir === 'asc' ? 1 : -1) * compare(a, b, sort.key) || collator.compare(a.label, b.label))
    : filtered;
  const types = [...new Set((data?.devices ?? []).map((d) => d.type))].sort();
  const now = data?.now_ms ?? Date.now();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-white">Devices</h1>
        {status.data && (
          <span className="text-sm text-slate-400">
            {status.data.devices.online} online · {status.data.devices.total} known
          </span>
        )}
      </div>
      {status.data && <DiscoveryPanel status={status.data} refresh={status.refresh} />}
      {suggestQ.data && suggestQ.data.length > 0 && <Suggestions items={suggestQ.data} onDone={reloadAll} />}
      {groups.length > 0 && <GroupStrip selected={groupFilter} onSelect={(id) => setFilter('group', id)} />}
      {status.error && <p className="text-rose-400">{status.error}</p>}

      <div className="flex flex-wrap items-center gap-3">
        <input aria-label="Search devices" placeholder="Search name, IP, MAC, vendor…" className={`${input} w-full sm:w-64`}
          value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Type" className={input} value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All types</option>
          {types.map((t) => <option key={t} value={t}>{DEVICE_TYPE_LABEL[t] ?? t}</option>)}
        </select>
        <select aria-label="Group" className={input} value={groupFilter} onChange={(e) => setFilter('group', e.target.value)}>
          <option value="">All groups</option>
          {groups.map((g) => <option key={g.id} value={String(g.id)}>{g.name} ({g.devices})</option>)}
          <option value="0">No group</option>
        </select>
        {tags.length > 0 && (
          <select aria-label="Tag filter" className={input} value={tagFilter} onChange={(e) => setFilter('tag', e.target.value)}>
            <option value="">All tags</option>
            {tags.map((t) => <option key={t} value={t}>#{t}</option>)}
          </select>
        )}
        <label className="inline-flex items-center gap-1.5 text-sm text-slate-300">
          <input type="checkbox" checked={onlineOnly} onChange={(e) => setOnlineOnly(e.target.checked)} />
          Online only
        </label>
        <button className={`${btn} ml-auto bg-slate-800 text-slate-200 hover:bg-slate-700`} onClick={() => setManaging(true)}>Groups…</button>
      </div>
      {selected.size > 0 && (
        <BulkBar ids={[...selected]} groups={groups} onDone={reloadAll} onClear={() => setSelected(new Set())} />
      )}
      {managing && <GroupManager groups={groups} onChanged={reloadAll} onClose={() => setManaging(false)} />}
      {error && <p className="text-rose-400">{error}</p>}

      <div className="overflow-x-auto rounded-lg ring-1 ring-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-8 px-3 py-2">
                <input type="checkbox" aria-label="Select all shown" checked={rows.length > 0 && rows.every((d) => selected.has(d.id))}
                  onChange={(e) => setSelected(e.target.checked ? new Set(rows.map((d) => d.id)) : new Set())} />
              </th>
              <SortHeader k="device" label="Device" sort={sort} onSort={onSort} />
              <th className="hidden px-3 py-2 lg:table-cell">Group and tags</th>
              <SortHeader k="type" label="Type" sort={sort} onSort={onSort} className="hidden sm:table-cell" />
              <SortHeader k="ip" label="IP" sort={sort} onSort={onSort} />
              <SortHeader k="mac" label="MAC / vendor" sort={sort} onSort={onSort} className="hidden md:table-cell" />
              <SortHeader k="seen" label="Last seen" sort={sort} onSort={onSort} className="text-right" align="right" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {rows.map((d) => (
              <tr key={d.id} className={`hover:bg-slate-900/60 ${selected.has(d.id) ? 'bg-slate-900' : ''}`}>
                <td className="w-8 px-3 py-2 align-top">
                  <input type="checkbox" aria-label={`Select ${d.label}`} checked={selected.has(d.id)}
                    onChange={() => setSelected((cur) => { const n = new Set(cur); if (n.has(d.id)) n.delete(d.id); else n.add(d.id); return n; })} />
                </td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${d.online ? 'bg-emerald-400' : 'bg-slate-600'}`}
                      title={d.online ? 'online' : 'offline'} />
                    <Link to={`/devices/${d.id}`} className="font-medium text-slate-100 hover:text-sky-300">{d.label}</Link>
                    {d.target_id && <span className="rounded bg-sky-900/60 px-1.5 text-[10px] text-sky-200">monitored</span>}
                  </div>
                  {names(d) && <div className="ml-4 text-xs text-slate-500">{names(d)}</div>}
                  <div className="ml-4 text-xs text-slate-500 sm:hidden">
                    {DEVICE_TYPE_LABEL[d.type] ?? d.type}
                    {d.vendor ? ` · ${d.vendor}` : ''}
                  </div>
                  {(d.group || d.tags?.length > 0) && (
                    <div className="ml-4 mt-1 flex flex-wrap gap-1 lg:hidden">
                      {d.group && <GroupBadge group={d.group} />}
                      {d.tags?.map((t) => <TagChip key={t} tag={t} />)}
                    </div>
                  )}
                </td>
                <td className="hidden px-3 py-2 lg:table-cell">
                  <div className="flex flex-wrap gap-1">
                    {d.group && <GroupBadge group={d.group} />}
                    {d.tags?.map((t) => <TagChip key={t} tag={t} />)}
                  </div>
                </td>
                <td className="hidden px-3 py-2 text-slate-300 sm:table-cell" title={d.type_override ? 'set by you' : d.type_reason ?? undefined}>
                  {DEVICE_TYPE_LABEL[d.type] ?? d.type}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-slate-300">{d.last_ip ?? '—'}</td>
                <td className="hidden px-3 py-2 text-xs md:table-cell">
                  <span className="font-mono text-slate-400">{d.macs[0]}</span>
                  {d.macs.length > 1 && <span className="text-slate-500"> +{d.macs.length - 1}</span>}
                  <div className="text-slate-500">{d.vendor ?? (d.randomised ? 'Private (randomised) address' : 'Unknown vendor')}</div>
                </td>
                <td className="px-3 py-2 text-right text-xs text-slate-400" title={fmtTime(d.last_seen_ms)}>
                  {d.online ? 'now' : fmtAgo(d.last_seen_ms, now)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && rows.length === 0 && (
          <p className="p-6 text-center text-sm text-slate-500">
            {data.devices.length === 0 ? 'No devices seen yet. Devices appear as they talk on the network.' : 'No device matches.'}
          </p>
        )}
      </div>
    </div>
  );
}
