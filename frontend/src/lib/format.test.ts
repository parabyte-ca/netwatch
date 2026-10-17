import { describe, expect, it } from 'vitest';
import { fmtAgo, fmtAxisMs, fmtBytes, fmtDuration, fmtMs, fmtPct, lossColour } from './format';
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
