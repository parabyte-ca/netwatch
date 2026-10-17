import type { State } from './api';

export function fmtMs(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (v >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${v.toFixed(v < 10 ? Math.max(digits, 2) : digits)} ms`;
}

/** Compact axis label: "0 ms", "0.5 ms", "25 ms", "1.5 s". */
export function fmtAxisMs(v: number): string {
  if (v >= 1000) return `${+(v / 1000).toFixed(2)} s`;
  return `${+v.toFixed(2)} ms`;
}

export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${v.toFixed(v > 0 && v < 1 ? 2 : v < 10 ? 1 : 0)} %`;
}

export function fmtBytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtAgo(tsMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - tsMs) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export const STATE_STYLE: Record<State, { label: string; dot: string; text: string; ring: string }> = {
  up: { label: 'Up', dot: 'bg-emerald-400', text: 'text-emerald-300', ring: 'ring-emerald-500/30' },
  degraded: { label: 'Degraded', dot: 'bg-amber-400', text: 'text-amber-300', ring: 'ring-amber-500/40' },
  down: { label: 'Down', dot: 'bg-rose-500', text: 'text-rose-300', ring: 'ring-rose-500/50' },
  unknown: { label: 'No data', dot: 'bg-slate-500', text: 'text-slate-400', ring: 'ring-slate-600/40' },
};

/** SmokePing-style colour for a loss percentage (green → red). */
export function lossColour(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return '#64748b';
  if (pct === 0) return '#22c55e';
  if (pct <= 2) return '#84cc16';
  if (pct <= 5) return '#eab308';
  if (pct <= 15) return '#f97316';
  return '#ef4444';
}

export const GROUP_LABEL: Record<string, string> = {
  gateway: 'Gateway',
  mesh: 'Mesh nodes',
  public: 'Internet',
  dns: 'DNS',
  device: 'Devices',
  custom: 'Custom',
};

export const GROUP_ORDER = ['gateway', 'mesh', 'public', 'dns', 'device', 'custom'];
