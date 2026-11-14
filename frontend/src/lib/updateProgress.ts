import type { UpdateProgressInfo, UpdateStage } from './api';

/** An update started from this browser. NetWatch restarts during it, so this is kept in
 * sessionStorage: the page and the top bar keep going while the server does not answer. */
export interface PendingUpdate {
  to: string | null;
  from: string;
  action: 'update' | 'rollback';
  at: number;
}

const PENDING_KEY = 'nw-update-pending';
const ESTIMATE_KEY = 'nw-update-estimate-s';
const PENDING_MAX_MS = 45 * 60_000;
/** Without any history: the agent's average wait plus a typical pull and restart. */
export const DEFAULT_ESTIMATE_S = 210;
/** Past this, suggest looking at the agent on the host. */
export const SLOW_MS = 15 * 60_000;
/** A finished update stays on screen this long (the success or failure view). */
export const RECENT_MS = 10 * 60_000;

export function readPending(now = Date.now()): PendingUpdate | null {
  try {
    const p = JSON.parse(sessionStorage.getItem(PENDING_KEY) || 'null') as PendingUpdate | null;
    return p && typeof p.at === 'number' && now - p.at < PENDING_MAX_MS ? p : null;
  } catch {
    return null;
  }
}

export function writePending(p: PendingUpdate | null) {
  try {
    if (p) sessionStorage.setItem(PENDING_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* private mode: the page still works, it just forgets on refresh */
  }
}

/** The server's estimate, kept for while it restarts and cannot be asked. */
export function rememberEstimate(s: number | null | undefined) {
  try {
    if (s && s > 0) localStorage.setItem(ESTIMATE_KEY, String(Math.round(s)));
  } catch {
    /* not remembered */
  }
}

export function knownEstimate(s: number | null | undefined): number {
  if (s && s > 0) return s;
  try {
    const v = Number(localStorage.getItem(ESTIMATE_KEY));
    if (v > 0) return v;
  } catch {
    /* fall through */
  }
  return DEFAULT_ESTIMATE_S;
}

/** Stage list to show when the server sent none (an older server, or it is restarting). */
export const UPDATE_STAGES: UpdateStage[] = [
  ['requested', 'Requested'], ['started', 'Agent started'], ['backup', 'Backup'],
  ['download', 'Download and verify'], ['restart', 'Restart'], ['health', 'Health check'], ['done', 'Done'],
].map(([key, label], i) => ({ key: key!, label: label!, at: null, reached: i === 0 }));

export const parseTs = (v?: string | null) => {
  const t = v ? Date.parse(v) : NaN;
  return Number.isNaN(t) ? null : t;
};

/** When this update began, in ms: this browser's click, else the request or the start. */
export function startedAt(p: UpdateProgressInfo | null | undefined, pending: PendingUpdate | null): number | null {
  return pending?.at ?? parseTs(p?.request?.requested_at) ?? parseTs(p?.status?.started_at);
}

/** 0..1. Each stage owns an equal slice of the bar; inside the current slice the bar moves with
 * time against the estimate, but never into the next slice before that stage is reached. */
export function progressFraction(stages: UpdateStage[], elapsedS: number, estimateS: number, done: boolean): number {
  if (done) return 1;
  const n = Math.max(2, stages.length);
  let current = 0;
  stages.forEach((s, i) => {
    if (s.reached) current = i;
  });
  const floor = current / (n - 1);
  const ceiling = (current + 1) / (n - 1) - 0.015;
  const byTime = Math.min(0.97, Math.max(0, elapsedS / Math.max(1, estimateS)));
  return Math.min(0.98, Math.max(floor, Math.min(byTime, ceiling)));
}

export function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export const isActive = (state?: string) => state === 'requested' || state === 'running';
