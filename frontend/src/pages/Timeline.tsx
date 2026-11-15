import { useState } from 'react';
import { EventRow } from '../components/EventRow';
import { api, type EventKind, type TimelineEvent } from '../lib/api';
import { EVENT_LABEL } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const SPANS = [
  { key: '24h', label: '24 hours', s: 86_400 },
  { key: '7d', label: '7 days', s: 604_800 },
  { key: '30d', label: '30 days', s: 2_592_000 },
  { key: '90d', label: '90 days', s: 7_776_000 },
];
const KINDS: EventKind[] = ['outage', 'degraded', 'anomaly', 'nic_link', 'nic_speed', 'nic_errors', 'hub', 'dns', 'exposure', 'wifi', 'device', 'monitor', 'security', 'presence'];

function dayKey(ms: number) {
  return new Date(ms).toLocaleDateString('en-CA', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
}

export function Timeline() {
  const [span, setSpan] = useState(SPANS[1]!);
  const [kinds, setKinds] = useState<EventKind[]>(KINDS.filter((k) => k !== 'monitor' && k !== 'security'));
  const [group, setGroup] = useState('');
  const [tag, setTag] = useState('');
  const [showQuiet, setShowQuiet] = useState(false);
  const groupsQ = usePolling(api.deviceGroups, 120_000);
  const tagsQ = usePolling(api.deviceTags, 120_000);
  const { data, error } = usePolling(
    () => api.events({
      start: Math.floor(Date.now() / 1000) - span.s, kind: kinds, limit: 1000,
      group_id: group ? Number(group) : undefined, tag: tag || undefined, include_quiet: showQuiet,
    }),
    15_000,
    [span.key, kinds.join(','), group, tag, showQuiet],
  );

  const groups = new Map<string, TimelineEvent[]>();
  for (const ev of data ?? []) {
    const k = dayKey(ev.start_ms);
    groups.set(k, [...(groups.get(k) ?? []), ev]);
  }
  const toggle = (k: EventKind) => setKinds((cur) => (cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-white">Timeline</h1>
        <div className="flex gap-1" role="tablist" aria-label="Time span">
          {SPANS.map((s) => (
            <button key={s.key} role="tab" aria-selected={s.key === span.key} onClick={() => setSpan(s)}
              className={`rounded-md px-3 py-1 text-xs ${s.key === span.key ? 'bg-sky-600 text-white' : 'bg-slate-900 text-slate-400 hover:text-white'}`}>
              {s.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-slate-300" aria-label="Event types">
        {KINDS.map((k) => (
          <label key={k} className="inline-flex items-center gap-1.5">
            <input type="checkbox" checked={kinds.includes(k)} onChange={() => toggle(k)} />
            {EVENT_LABEL[k]}
          </label>
        ))}
      </div>
      {(groupsQ.data?.groups ?? []).some((g) => g.quiet) && (
        <label className="inline-flex items-center gap-1.5 text-xs text-slate-300">
          <input type="checkbox" checked={showQuiet} onChange={(e) => setShowQuiet(e.target.checked)} />
          Show quiet events (alerts for devices in quiet groups)
        </label>
      )}
      {((groupsQ.data?.groups.length ?? 0) > 0 || (tagsQ.data?.length ?? 0) > 0) && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-300">
          <span>Only events about</span>
          <select aria-label="Group filter" className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1" value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="">any device</option>
            {(groupsQ.data?.groups ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
          {(tagsQ.data?.length ?? 0) > 0 && (
            <select aria-label="Tag filter" className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1" value={tag} onChange={(e) => setTag(e.target.value)}>
              <option value="">any tag</option>
              {(tagsQ.data ?? []).map((t) => <option key={t.tag} value={t.tag}>#{t.tag}</option>)}
            </select>
          )}
        </div>
      )}
      {error && <p className="text-rose-400">{error}</p>}
      {data && data.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-400">
          No events in this period. Quiet is good.
        </p>
      )}
      {[...groups.entries()].map(([day, items]) => (
        <section key={day} aria-label={day}>
          <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{day}</h2>
          <div className="rounded-lg bg-slate-900 p-1 ring-1 ring-slate-800">
            {items.map((ev) => <EventRow key={ev.id} ev={ev} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
