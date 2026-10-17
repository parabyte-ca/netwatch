export interface RangeOption {
  key: string;
  label: string;
  seconds: number;
  refreshMs: number;
}

export const RANGES: RangeOption[] = [
  { key: '15m', label: '15 min', seconds: 900, refreshMs: 5_000 },
  { key: '1h', label: '1 hour', seconds: 3_600, refreshMs: 10_000 },
  { key: '6h', label: '6 hours', seconds: 21_600, refreshMs: 30_000 },
  { key: '24h', label: '24 hours', seconds: 86_400, refreshMs: 60_000 },
  { key: '7d', label: '7 days', seconds: 604_800, refreshMs: 300_000 },
  { key: '30d', label: '30 days', seconds: 2_592_000, refreshMs: 600_000 },
  { key: '90d', label: '90 days', seconds: 7_776_000, refreshMs: 600_000 },
];

export function findRange(key: string | null | undefined): RangeOption {
  return RANGES.find((r) => r.key === key) ?? RANGES[1]!;
}

export function windowFor(range: RangeOption, nowSec = Math.floor(Date.now() / 1000)) {
  return { start: nowSec - range.seconds, end: nowSec + 1 };
}
