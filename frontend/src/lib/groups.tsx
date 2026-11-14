import type { GroupRef } from './api';

/** Group colours: Tailwind classes for chips and dots, and a hex value for the map. */
export const GROUP_COLOURS: Record<string, { dot: string; chip: string; bar: string; hex: string }> = {
  sky: { dot: 'bg-sky-400', chip: 'bg-sky-950 text-sky-200 ring-sky-800', bar: 'border-l-sky-400', hex: '#38bdf8' },
  emerald: { dot: 'bg-emerald-400', chip: 'bg-emerald-950 text-emerald-200 ring-emerald-800', bar: 'border-l-emerald-400', hex: '#34d399' },
  amber: { dot: 'bg-amber-400', chip: 'bg-amber-950 text-amber-200 ring-amber-800', bar: 'border-l-amber-400', hex: '#fbbf24' },
  rose: { dot: 'bg-rose-400', chip: 'bg-rose-950 text-rose-200 ring-rose-800', bar: 'border-l-rose-400', hex: '#fb7185' },
  violet: { dot: 'bg-violet-400', chip: 'bg-violet-950 text-violet-200 ring-violet-800', bar: 'border-l-violet-400', hex: '#a78bfa' },
  cyan: { dot: 'bg-cyan-400', chip: 'bg-cyan-950 text-cyan-200 ring-cyan-800', bar: 'border-l-cyan-400', hex: '#22d3ee' },
  lime: { dot: 'bg-lime-400', chip: 'bg-lime-950 text-lime-200 ring-lime-800', bar: 'border-l-lime-400', hex: '#a3e635' },
  orange: { dot: 'bg-orange-400', chip: 'bg-orange-950 text-orange-200 ring-orange-800', bar: 'border-l-orange-400', hex: '#fb923c' },
  pink: { dot: 'bg-pink-400', chip: 'bg-pink-950 text-pink-200 ring-pink-800', bar: 'border-l-pink-400', hex: '#f472b6' },
  slate: { dot: 'bg-slate-400', chip: 'bg-slate-800 text-slate-200 ring-slate-600', bar: 'border-l-slate-400', hex: '#94a3b8' },
};

export const colourOf = (key?: string | null) => GROUP_COLOURS[key ?? ''] ?? GROUP_COLOURS.slate!;

/** Small stroke icons (24 x 24 paths). */
const ICON_PATHS: Record<string, string> = {
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  family: 'M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm8 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2 20c0-3 3-5 6-5s6 2 6 5M12 20c0-3 2-5 4-5s6 2 6 5',
  child: 'M12 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8 21v-6l-2-3 3-1h6l3 1-2 3v6M10 21v-4h4v4',
  tv: 'M3 6h18v11H3zM8 21h8M12 17v4',
  plug: 'M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5',
  camera: 'M3 8h4l2-3h6l2 3h4v11H3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  work: 'M3 7h18v12H3zM9 7V4h6v3M3 12h18',
  game: 'M6 9h12a4 4 0 0 1 0 8l-2-2H8l-2 2a4 4 0 0 1 0-8zM8 11v4M6 13h4M16 12h.01M18 14h.01',
  star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
};

export function GroupIcon({ icon, className = 'h-3.5 w-3.5' }: { icon?: string | null; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={ICON_PATHS[icon ?? ''] ?? ICON_PATHS.star!} />
    </svg>
  );
}

export function GroupBadge({ group }: { group: GroupRef }) {
  const c = colourOf(group.colour);
  return (
    <span className={`inline-flex max-w-full items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ${c.chip}`} title={group.name}>
      <GroupIcon icon={group.icon} className="h-3 w-3 shrink-0" />
      <span className="truncate">{group.name}</span>
    </span>
  );
}

export function TagChip({ tag, onRemove }: { tag: string; onRemove?: () => void }) {
  return (
    <span className="inline-flex items-center gap-0.5 rounded-full bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-300">
      #{tag}
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label={`Remove tag ${tag}`} className="ml-0.5 text-slate-500 hover:text-white">×</button>
      )}
    </span>
  );
}

export const ICON_NAMES = Object.keys(ICON_PATHS);
