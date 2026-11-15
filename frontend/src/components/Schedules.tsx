import { useState } from 'react';
import { api, type ActiveSchedule, type Day, type Schedule, type ScheduleEffects, type ScheduleInput, type SchedulesState, type ScheduleTarget } from '../lib/api';
import { usePolling } from '../lib/usePolling';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const input = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const DAYS: Day[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const DAY_LABEL: Record<Day, string> = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };
const CAVEAT = 'Devices keep DNS answers they already have for a few minutes, and a device that uses its own encrypted DNS goes around AdGuard Home.';

const clock = (ms: number) => new Date(ms).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit', hour12: false });
const dayClock = (ms: number) => new Date(ms).toLocaleString('en-CA', { weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false });

/** "Sun to Thu", "Every day", "Weekdays", or "Mon, Wed". */
export function daysText(days: Day[]): string {
  const set = new Set(days);
  if (set.size === 7) return 'Every day';
  if (['mon', 'tue', 'wed', 'thu', 'fri'].every((d) => set.has(d as Day)) && set.size === 5) return 'Weekdays';
  if (set.size === 2 && set.has('sat') && set.has('sun')) return 'Weekends';
  // A run in week order starting on Sunday (school nights) reads best as "Sun to Thu".
  const order: Day[] = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
  const idx = order.map((d) => set.has(d));
  const first = idx.indexOf(true);
  const last = idx.lastIndexOf(true);
  if (set.size >= 3 && idx.slice(first, last + 1).every(Boolean)) return `${DAY_LABEL[order[first]!]} to ${DAY_LABEL[order[last]!]}`;
  return DAYS.filter((d) => set.has(d)).map((d) => DAY_LABEL[d]).join(', ');
}

/** Schedules and what is in force now, while AdGuard Home actions are on; else null. */
export function useSchedules() {
  const q = usePolling(api.schedules, 30_000);
  const ok = !q.error && !!q.data && Array.isArray(q.data.schedules);
  return { state: ok ? q.data : null, refresh: q.refresh };
}

const blank = (target: ScheduleTarget): ScheduleInput => ({
  name: '', target, days: ['sun', 'mon', 'tue', 'wed', 'thu'], start: '21:00', end: '07:00',
  effects: { internet_off: true, youtube_off: false, strict: false, services: [] }, enabled: true,
});

function ScheduleEditor({ value, presets, services, onSave, onCancel }: {
  value: ScheduleInput; presets: SchedulesState['presets']; services: { id: string; name: string }[];
  onSave: (s: ScheduleInput) => void; onCancel: () => void;
}) {
  const [s, setS] = useState<ScheduleInput>(value);
  const fx = (patch: Partial<ScheduleEffects>) => setS({ ...s, effects: { ...s.effects, ...patch } });
  const names = Object.fromEntries(services.map((x) => [x.id, x.name]));
  const left = services.filter((x) => x.id !== 'youtube' && !s.effects.services.includes(x.id)).sort((a, b) => a.name.localeCompare(b.name));
  const nothing = !s.effects.internet_off && !s.effects.youtube_off && !s.effects.strict && !s.effects.services.length;
  return (
    <div className="space-y-2 rounded-md bg-slate-950 p-3 text-sm ring-1 ring-slate-800" role="group" aria-label="Schedule editor">
      <div className="flex flex-wrap gap-1.5">
        <span className="text-xs text-slate-400">Start from:</span>
        {presets.map((p) => (
          <button key={p.name} className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`}
            onClick={() => setS({ ...s, name: p.name, days: p.days, start: p.start, end: p.end,
              effects: { internet_off: false, youtube_off: false, strict: false, services: [], ...p.effects } })}>{p.name}</button>
        ))}
      </div>
      <label className="flex flex-col gap-1 text-xs text-slate-400">Name
        <input aria-label="Schedule name" className={`${input} w-56`} maxLength={40} value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} />
      </label>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Days">
        {DAYS.map((d) => {
          const on = s.days.includes(d);
          return (
            <button key={d} aria-pressed={on} onClick={() => setS({ ...s, days: on ? s.days.filter((x) => x !== d) : [...s.days, d] })}
              className={`${btn} ${on ? 'bg-sky-600 text-white' : 'text-slate-300 ring-1 ring-slate-700'}`}>{DAY_LABEL[d]}</button>
          );
        })}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-slate-400">From
          <input aria-label="Start time" type="time" className={input} value={s.start} onChange={(e) => setS({ ...s, start: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">To
          <input aria-label="End time" type="time" className={input} value={s.end} onChange={(e) => setS({ ...s, end: e.target.value })} />
        </label>
        {s.end <= s.start && s.start !== s.end && <span className="pb-1 text-xs text-slate-500">ends the next morning</span>}
      </div>
      <fieldset className="space-y-1">
        <legend className="text-xs text-slate-400">While it is on</legend>
        <label className="flex items-center gap-2"><input type="checkbox" checked={s.effects.internet_off} onChange={(e) => fx({ internet_off: e.target.checked })} /> Internet off</label>
        {!s.effects.internet_off && (
          <>
            <label className="flex items-center gap-2"><input type="checkbox" checked={s.effects.youtube_off} onChange={(e) => fx({ youtube_off: e.target.checked })} /> YouTube off</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={s.effects.strict} onChange={(e) => fx({ strict: e.target.checked })} /> Strict filtering</label>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-slate-400">Block apps:</span>
              {s.effects.services.map((id) => (
                <span key={id} className="inline-flex items-center gap-1 rounded-full bg-rose-950 px-2 py-0.5 text-xs text-rose-200 ring-1 ring-rose-900">
                  {names[id] ?? id}
                  <button aria-label={`Stop blocking ${names[id] ?? id}`} onClick={() => fx({ services: s.effects.services.filter((x) => x !== id) })}>×</button>
                </span>
              ))}
              {left.length > 0 && (
                <select aria-label="Add an app to the schedule" className={`${input} py-0.5 text-xs`} value=""
                  onChange={(e) => e.target.value && fx({ services: [...s.effects.services, e.target.value] })}>
                  <option value="">Add an app…</option>
                  {left.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </select>
              )}
            </div>
          </>
        )}
      </fieldset>
      <p className="text-xs text-slate-500">{CAVEAT}</p>
      <div className="flex gap-2">
        <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!s.name.trim() || !s.days.length || s.start === s.end || nothing}
          onClick={() => onSave(s)}>Save schedule</button>
        <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function scheduleWhen(s: Schedule, active: boolean): string {
  if (s.off_until_ms) return `Off until ${dayClock(s.off_until_ms)}`;
  if (!s.enabled) return 'Turned off';
  if (active && s.next) return `On now until ${clock(s.next.end_ms)}`;
  return s.next ? `Next: ${dayClock(s.next.start_ms)}` : 'No window this week';
}

/** A group's or a device's schedules: list, add, edit, exceptions, and "internet off now". */
export function TargetSchedules({ target, label, services }: { target: ScheduleTarget; label: string; services: { id: string; name: string }[] }) {
  const { state, refresh } = useSchedules();
  const [editing, setEditing] = useState<Schedule | 'new' | null>(null);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!state) return null;
  const mine = state.schedules.filter((s) => s.target.kind === target.kind && s.target.id === target.id);
  const activeIds = new Set(state.active.filter((a) => a.target.kind === target.kind && a.target.id === target.id).map((a) => a.id));
  const manual = state.active.find((a) => a.manual && a.target.kind === target.kind && a.target.id === target.id);
  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); await refresh(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const confirmSave = (s: ScheduleInput, id?: string) => setAsk({
    title: id ? `Save the schedule ${s.name}?` : `Add the schedule ${s.name} for ${label}?`,
    body: <><p>{daysText(s.days)} {s.start} to {s.end}: {s.effects.internet_off ? 'internet off' : 'stricter settings'}.</p><p className="text-xs text-slate-400">{CAVEAT} Written to the security log.</p></>,
    confirmLabel: 'Save', run: async () => { await (id ? api.updateSchedule(id, s) : api.addSchedule(s)); setEditing(null); await refresh(); },
  });
  return (
    <div className="space-y-2" aria-label={`Schedules for ${label}`} role="region">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Schedules</h3>
      {mine.length === 0 && editing === null && <p className="text-xs text-slate-500">No schedules yet.</p>}
      <ul className="space-y-1.5">
        {mine.map((s) => {
          const on = activeIds.has(s.id);
          return (
            <li key={s.id} className={`rounded-md p-2 text-sm ring-1 ${on ? 'bg-amber-950/40 ring-amber-800' : 'bg-slate-950 ring-slate-800'}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-100">{s.name}</span>
                <span className="text-xs text-slate-400">{daysText(s.days)} {s.start}–{s.end} · {s.summary}</span>
                <span className={`text-xs ${on ? 'text-amber-200' : 'text-slate-500'}`}>{scheduleWhen(s, on)}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {on && <>
                  <button className={`${btn} bg-amber-700 text-white hover:bg-amber-600`} onClick={() => void run(() => api.extendSchedule(s.id, '15'))}>+15 min</button>
                  <button className={`${btn} text-amber-200 ring-1 ring-amber-800`} onClick={() => void run(() => api.extendSchedule(s.id, '60'))}>+1 hour</button>
                  <button className={`${btn} text-amber-200 ring-1 ring-amber-800`} onClick={() => void run(() => api.extendSchedule(s.id, 'until_next'))}>Off until it next starts</button>
                </>}
                {!on && !s.off_until_ms && s.enabled && <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={() => void run(() => api.extendSchedule(s.id, 'skip'))}>Skip today</button>}
                {s.off_until_ms && <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={() => void run(() => api.resumeSchedule(s.id))}>Back on now</button>}
                <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={() => void run(() => api.updateSchedule(s.id, { ...s, enabled: !s.enabled }))}>{s.enabled ? 'Turn off' : 'Turn on'}</button>
                <button className={`${btn} text-sky-300`} onClick={() => setEditing(s)}>Edit</button>
                <button className={`${btn} text-rose-300`} onClick={() => setAsk({ title: `Delete the schedule ${s.name}?`, body: <p>{label} goes back to its usual settings.</p>, confirmLabel: 'Delete', danger: true, run: async () => { await api.deleteSchedule(s.id); await refresh(); } })}>Delete</button>
              </div>
            </li>
          );
        })}
      </ul>
      {editing !== null ? (
        <ScheduleEditor value={editing === 'new' ? blank(target) : editing} presets={state.presets} services={services}
          onSave={(s) => confirmSave({ ...s, target }, editing === 'new' ? undefined : editing.id)} onCancel={() => setEditing(null)} />
      ) : (
        <button className={`${btn} bg-slate-800 text-slate-100 hover:bg-slate-700`} onClick={() => setEditing('new')}>Add a schedule</button>
      )}
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        {manual ? (
          <>
            <span className="text-xs text-amber-200">Internet off until {clock(manual.end_ms)}</span>
            <button className={`${btn} bg-emerald-700 text-white hover:bg-emerald-600`} onClick={() => void run(() => api.internetBackOn(manual.id))}>Back on now</button>
          </>
        ) : (
          <>
            <span className="text-xs text-slate-400">Internet off now:</span>
            {([30, 60, 120] as const).map((m) => (
              <button key={m} className={`${btn} text-rose-200 ring-1 ring-rose-900 hover:bg-rose-950`}
                onClick={() => setAsk({ title: `Turn the internet off for ${label} for ${m === 30 ? '30 min' : m === 60 ? '1 hour' : '2 hours'}?`, body: <p className="text-xs text-slate-400">{CAVEAT}</p>, confirmLabel: 'Turn off', danger: true, run: async () => { await api.internetOffNow(target, m); await refresh(); } })}>
                {m === 30 ? '30 min' : m === 60 ? '1 h' : '2 h'}
              </button>
            ))}
          </>
        )}
      </div>
      {err && <p className="text-xs text-rose-300">{err}</p>}
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </div>
  );
}

/** Overview: what is in force right now, with the quick exceptions. */
export function ActiveNow() {
  const { state, refresh } = useSchedules();
  const [err, setErr] = useState<string | null>(null);
  if (!state || state.active.length === 0) return null;
  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try { await fn(); await refresh(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <section aria-label="Schedules active now" className="space-y-1.5 rounded-lg bg-amber-950/30 p-3 ring-1 ring-amber-900">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-amber-200">Active now</h2>
      {state.active.map((a: ActiveSchedule) => (
        <div key={`${a.id}-${a.target.kind}-${a.target.id}`} className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium text-slate-100">{a.target_label}</span>
          <span className="text-slate-300">{a.effects.internet_off ? 'internet off' : 'stricter settings'} ({a.name}) until {clock(a.end_ms)}</span>
          {a.manual ? (
            <button className={`${btn} bg-emerald-700 text-white hover:bg-emerald-600`} onClick={() => void run(() => api.internetBackOn(a.id))}>Back on now</button>
          ) : (
            <button className={`${btn} bg-amber-700 text-white hover:bg-amber-600`} onClick={() => void run(() => api.extendSchedule(a.id, '15'))}>+15 min</button>
          )}
        </div>
      ))}
      {err && <p className="text-xs text-rose-300">{err}</p>}
    </section>
  );
}

/** DNS page: every schedule. */
export function ScheduleList() {
  const { state } = useSchedules();
  if (!state) return null;
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="Schedules">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Schedules</h2>
      {state.schedules.length === 0 ? (
        <p className="text-sm text-slate-400">None yet. Add one in a group's AdGuard Home settings (Devices → Groups…) or on a device's page.</p>
      ) : (
        <ul className="divide-y divide-slate-800 text-sm">
          {state.schedules.map((s) => {
            const on = state.active.some((a) => a.id === s.id);
            return (
              <li key={s.id} className="flex flex-wrap items-center gap-2 py-1.5">
                <span className="font-medium text-slate-100">{s.target_label}</span>
                <span className="text-slate-300">{s.name}</span>
                <span className="text-xs text-slate-400">{daysText(s.days)} {s.start}–{s.end} · {s.summary}</span>
                <span className={`text-xs ${on ? 'text-amber-200' : 'text-slate-500'}`}>{scheduleWhen(s, on)}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-2 text-xs text-slate-500">Times are in {state.tz}. {CAVEAT}</p>
    </section>
  );
}
