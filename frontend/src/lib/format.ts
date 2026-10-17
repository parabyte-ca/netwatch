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

/** Human duration from milliseconds: "45 s", "12 min", "3 h 05 min", "2 d 4 h". */
export function fmtDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return 'ongoing';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ${String(m % 60).padStart(2, '0')} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

export function fmtBps(bps: number | null | undefined): string {
  if (bps === null || bps === undefined) return '—';
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(2)} Gb/s`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(bps >= 1e8 ? 0 : 1)} Mb/s`;
  return `${(bps / 1e3).toFixed(0)} kb/s`;
}

export function fmtTime(ms: number): string {
  return new Date(ms).toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'medium' });
}

export const EVENT_LABEL: Record<string, string> = {
  outage: 'Outage',
  degraded: 'Packet loss',
  nic_link: 'NIC link',
  nic_speed: 'NIC speed',
  nic_errors: 'NIC errors',
  monitor: 'NetWatch',
  anomaly: 'Anomaly',
  device: 'Device',
  security: 'Security',
};

export const DEVICE_TYPE_LABEL: Record<string, string> = {
  router: 'Router',
  access_point: 'Access point',
  network: 'Network gear',
  nas: 'NAS',
  server: 'Server',
  computer: 'Computer',
  phone: 'Phone',
  tablet: 'Tablet',
  tv: 'TV',
  media_player: 'Media player',
  speaker: 'Speaker',
  printer: 'Printer',
  camera: 'Camera',
  game_console: 'Game console',
  iot: 'Smart home',
  unknown: 'Unknown',
};

export const SEVERITY_DOT: Record<string, string> = {
  critical: 'bg-rose-500',
  warning: 'bg-amber-400',
  info: 'bg-sky-400',
};
