import { Link } from 'react-router-dom';
import type { TimelineEvent } from '../lib/api';
import { EVENT_LABEL, fmtDurationMs, fmtTime, SEVERITY_DOT } from '../lib/format';

const clock = (ms: number) => new Date(ms).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' });

export function EventRow({ ev, compact = false }: { ev: TimelineEvent; compact?: boolean }) {
  const instant = ev.end_ms !== null && ev.end_ms === ev.start_ms;
  return (
    <Link to={`/events/${ev.id}`} className="flex items-start gap-3 rounded-md px-2 py-1.5 hover:bg-slate-800/60 sm:items-center">
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full sm:mt-0 ${SEVERITY_DOT[ev.severity] ?? 'bg-slate-500'} ${ev.open ? 'animate-pulse' : ''}`} />
      {/* phones: label and time above a title that may wrap; wider screens: one line */}
      <span className="min-w-0 flex-1 sm:flex sm:items-center sm:gap-3">
        <span className="block text-[11px] uppercase tracking-wide text-slate-500 sm:w-24 sm:shrink-0">
          {EVENT_LABEL[ev.kind] ?? ev.kind}
          <span className="normal-case sm:hidden"> · {clock(ev.start_ms)}</span>
        </span>
        <span className="block text-sm text-slate-200 sm:min-w-0 sm:flex-1 sm:truncate">{ev.title}</span>
        {!compact && <span className="hidden shrink-0 text-xs text-slate-500 sm:inline">{fmtTime(ev.start_ms)}</span>}
      </span>
      <span className={`shrink-0 text-right text-xs sm:w-28 ${ev.open ? 'text-rose-300' : 'text-slate-400'}`}>
        {instant ? 'instant' : ev.open ? `ongoing · ${fmtDurationMs(Date.now() - ev.start_ms)}` : fmtDurationMs(ev.duration_ms)}
      </span>
    </Link>
  );
}
