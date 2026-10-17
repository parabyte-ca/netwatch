import type { State } from '../lib/api';
import { STATE_STYLE } from '../lib/format';

export function StatusDot({ state }: { state: State }) {
  const s = STATE_STYLE[state];
  return (
    <span className="relative inline-flex h-2.5 w-2.5" aria-hidden>
      {state === 'down' && <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${s.dot}`} />}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${s.dot}`} />
    </span>
  );
}

export function StatusBadge({ state }: { state: State }) {
  const s = STATE_STYLE[state];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${s.text}`}>
      <StatusDot state={state} />
      {s.label}
    </span>
  );
}
