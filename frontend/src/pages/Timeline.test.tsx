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
      const url = String(input);
      if (url === '/api/device-groups') return new Response(JSON.stringify({ groups: [], colours: [], icons: [] }), { status: 200 });
      if (url === '/api/device-tags') return new Response('[]', { status: 200 });
      urls.push(url);
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

  it('filters by group when groups exist', async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/device-groups') {
        return new Response(JSON.stringify({ groups: [{ id: 3, name: 'Kids', colour: 'rose', icon: 'child', sort: 1, created_ms: 1, devices: 2 }], colours: [], icons: [] }), { status: 200 });
      }
      if (url === '/api/device-tags') return new Response('[]', { status: 200 });
      urls.push(url);
      return new Response('[]', { status: 200 });
    });
    render(<MemoryRouter><Timeline /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Group filter'), { target: { value: '3' } });
    await waitFor(() => expect(urls.at(-1)).toContain('group_id=3'));
  });

  it('hides quiet events unless asked, and marks them', async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/device-groups') {
        return new Response(JSON.stringify({ groups: [{ id: 5, name: 'Smart home', colour: 'emerald', icon: 'plug', quiet: true, sort: 1, created_ms: 1, devices: 9 }], colours: [], icons: [] }), { status: 200 });
      }
      if (url === '/api/device-tags') return new Response('[]', { status: 200 });
      urls.push(url);
      const quiet = { ...ev(7, 'device', 'Kasa plug changed address', Date.now() - 60_000, Date.now() - 60_000), severity: 'info' as const, details: { quiet: true } };
      return new Response(JSON.stringify(url.includes('include_quiet=false') ? [] : [quiet]), { status: 200 });
    });
    render(<MemoryRouter><Timeline /></MemoryRouter>);
    const box = await screen.findByLabelText(/Show quiet events/);
    await waitFor(() => expect(urls.at(-1)).toContain('include_quiet=false'));
    fireEvent.click(box);
    await waitFor(() => expect(urls.at(-1)).not.toContain('include_quiet'));
    expect(await screen.findByText('Kasa plug changed address')).toBeInTheDocument();
    expect(screen.getByText('quiet')).toBeInTheDocument();
  });
});
