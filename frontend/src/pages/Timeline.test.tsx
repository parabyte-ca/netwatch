import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TimelineEvent } from '../lib/api';
import { Timeline } from './Timeline';

afterEach(() => vi.restoreAllMocks());

const ev = (id: number, kind: TimelineEvent['kind'], title: string, start: number, end: number | null): TimelineEvent => ({
  id, kind, severity: kind === 'outage' ? 'critical' : 'warning', target_id: null, iface: null, start_ms: start, end_ms: end,
  duration_ms: end === null ? null : end - start, open: end === null, title, details: {},
});

describe('Timeline', () => {
  it('groups by day, shows durations and sends kind filters', async () => {
    const day = new Date('2026-10-17T15:00:00').getTime();
    const urls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      urls.push(String(input));
      return new Response(JSON.stringify([
        ev(1, 'outage', 'Gateway unreachable', day, day + 125_000),
        ev(2, 'nic_speed', 'enp3s0 renegotiated 2500 → 1000 Mb/s', day - 86_400_000, day - 86_400_000),
      ]), { status: 200 });
    });
    render(<MemoryRouter><Timeline /></MemoryRouter>);
    expect(await screen.findByText('Gateway unreachable')).toBeInTheDocument();
    expect(screen.getByText('2 min')).toBeInTheDocument();
    expect(screen.getByText('instant')).toBeInTheDocument();
    expect(screen.getAllByRole('region')).toHaveLength(2);
    expect(urls[0]).toContain('kind=outage');
    expect(urls[0]).not.toContain('kind=monitor');
    fireEvent.click(screen.getByLabelText('NetWatch'));
    await waitFor(() => expect(urls.at(-1)).toContain('kind=monitor'));
  });
});
