import { describe, expect, it } from 'vitest';
import { fmtAgo, fmtAxisMs, fmtBps, fmtBytes, fmtDuration, fmtDurationMs, fmtMs, fmtPct, lossColour } from './format';
import { findRange, windowFor } from './ranges';

describe('format', () => {
  it('formats milliseconds', () => {
    expect(fmtMs(null)).toBe('—');
    expect(fmtMs(0.456)).toBe('0.46 ms');
    expect(fmtMs(12.34)).toBe('12.3 ms');
    expect(fmtMs(1500)).toBe('1.50 s');
    expect(fmtAxisMs(0)).toBe('0 ms');
    expect(fmtAxisMs(0.5)).toBe('0.5 ms');
    expect(fmtAxisMs(1500)).toBe('1.5 s');
  });

  it('formats percentages', () => {
    expect(fmtPct(undefined)).toBe('—');
    expect(fmtPct(0)).toBe('0.0 %');
    expect(fmtPct(0.5)).toBe('0.50 %');
    expect(fmtPct(42)).toBe('42 %');
  });

  it('formats bytes, durations and ages', () => {
    expect(fmtBytes(512)).toBe('512 B');
    expect(fmtBytes(1536 * 1024)).toBe('1.5 MB');
    expect(fmtDuration(30)).toBe('30s');
    expect(fmtDuration(120)).toBe('2m');
    expect(fmtDuration(7200)).toBe('2h');
    expect(fmtAgo(0, 5_000)).toBe('5s ago');
    expect(fmtAgo(0, 7_200_000)).toBe('2h ago');
  });

  it('formats event durations and throughput', () => {
    expect(fmtDurationMs(null)).toBe('ongoing');
    expect(fmtDurationMs(45_000)).toBe('45 s');
    expect(fmtDurationMs(12 * 60_000)).toBe('12 min');
    expect(fmtDurationMs(185 * 60_000)).toBe('3 h 05 min');
    expect(fmtDurationMs(52 * 3_600_000)).toBe('2 d 4 h');
    expect(fmtBps(941_000_000)).toBe('941 Mb/s');
    expect(fmtBps(2_350_000_000)).toBe('2.35 Gb/s');
    expect(fmtBps(45_600_000)).toBe('45.6 Mb/s');
    expect(fmtBps(null)).toBe('—');
  });

  it('colours loss like SmokePing', () => {
    expect(lossColour(0)).toBe('#22c55e');
    expect(lossColour(50)).toBe('#ef4444');
    expect(lossColour(null)).toBe('#64748b');
  });
});

describe('ranges', () => {
  it('falls back to 1 hour and builds windows', () => {
    expect(findRange('nope').key).toBe('1h');
    expect(findRange('7d').seconds).toBe(604_800);
    expect(windowFor(findRange('15m'), 1000)).toEqual({ start: 100, end: 1001 });
  });
});
