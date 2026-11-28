import { useEffect, useState } from 'react';
import { api, type DecoSchedule } from '../lib/api';
import { fmtAgo } from '../lib/format';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function when(ms: number): string {
  return new Date(ms).toLocaleString('en-CA', { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/** Admin → Deco: restart the whole Wi-Fi network at a set time each day. */
export function DecoDailyRestart() {
  const [s, setS] = useState<DecoSchedule | null>(null);
  const [time, setTime] = useState('06:50');
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const apply = (v: DecoSchedule) => {
    setS(v);
    setTime(v.time);
    setDays(v.days);
  };
  useEffect(() => {
    api.decoSchedule().then((v) => (v && Array.isArray(v.days) ? apply(v) : undefined), () => undefined);
  }, []);

  if (!s || (!s.actions && !s.enabled)) return null;

  async function save(enabled: boolean) {
    setBusy(true);
    setErr(null);
    try {
      apply(await api.saveDecoSchedule({ enabled, time, days }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const dirty = time !== s.time || days.join() !== s.days.join();
  const toggleDay = (d: number) => setDays((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d].sort()));

  return (
    <section className="space-y-2 border-t border-slate-800 pt-3" aria-label="Daily restart">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Daily restart</h3>
      <label className="flex items-center gap-2 text-sm text-slate-200">
        <input type="checkbox" checked={s.enabled} disabled={busy || (!s.actions && !s.enabled)}
          onChange={(e) => void save(e.target.checked)} />
        Restart the whole Wi-Fi network every day
      </label>
      {!s.actions && s.enabled && (
        <p className="text-xs text-amber-300">Deco actions are off in the settings above, so nothing will run until they are turned on.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input type="time" aria-label="Restart time" value={time} onChange={(e) => setTime(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100" />
        {DAYS.map((label, d) => (
          <button key={label} type="button" aria-pressed={days.includes(d)} onClick={() => toggleDay(d)}
            className={`${btn} ${days.includes(d) ? 'bg-sky-600 text-white' : 'text-slate-300 ring-1 ring-slate-700'}`}>
            {label}
          </button>
        ))}
        <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={busy || !dirty || days.length === 0}
          onClick={() => void save(s.enabled)}>
          Save
        </button>
      </div>
      {s.enabled && s.next_ms && <p className="text-xs text-slate-400">Next: {when(s.next_ms)}.</p>}
      {s.last && <p className="text-xs text-slate-400">Last: {fmtAgo(s.last.ts_ms, Date.now())}, {s.last.summary}.</p>}
      <p className="text-xs text-slate-500">
        Wi-Fi drops for a few minutes, and this screen may stop answering. A run that NetWatch misses by more than 30 minutes is skipped.
      </p>
      {err && <p className="text-xs text-rose-300" role="alert">{err}</p>}
    </section>
  );
}
