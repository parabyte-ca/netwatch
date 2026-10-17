import { Link } from 'react-router-dom';
import type { TimelineEvent } from '../lib/api';
import { EVENT_LABEL, fmtDurationMs, fmtTime, SEVERITY_DOT } from '../lib/format';

export function EventRow({ ev, compact = false }: { ev: TimelineEvent; compact?: boolean }) {
  const instant = ev.end_ms !== null && ev.end_ms === ev.start_ms;
  return (
    <Link to={`/events/${ev.id}`} className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-slate-800/60">
      <span className={`h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[ev.severity] ?? 'bg-slate-500'} ${ev.open ? 'animate-pulse' : ''}`} />
      <span className="w-24 shrink-0 text-[11px] uppercase tracking-wide text-slate-500">{EVENT_LABEL[ev.kind] ?? ev.kind}</span>
      <span className="min-w-0 flex-1 truncate text-sm text-slate-200">{ev.title}</span>
      {!compact && <span className="hidden text-xs text-slate-500 sm:inline">{fmtTime(ev.start_ms)}</span>}
      <span className={`w-28 shrink-0 text-right text-xs ${ev.open ? 'text-rose-300' : 'text-slate-400'}`}>
        {instant ? 'instant' : ev.open ? `ongoing · ${fmtDurationMs(Date.now() - ev.start_ms)}` : fmtDurationMs(ev.duration_ms)}
      </span>
    </Link>
  );
}
