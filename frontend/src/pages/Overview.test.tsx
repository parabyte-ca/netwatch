import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { StatusResponse, Target, TimelineEvent } from '../lib/api';
import { Overview } from './Overview';

const base: Omit<Target, 'id' | 'name' | 'host' | 'group'> = {
  kind: 'icmp', port: null, url: null, dns_query: null, dns_rdtype: null, expect_status: null,
  interval_s: 1, timeout_s: 1, enabled: true, auto: true, created_at: 0, updated_at: 0,
};
const targets: Target[] = [
  { ...base, id: 1, name: 'Gateway', host: '192.168.2.1', group: 'gateway' },
  { ...base, id: 2, name: 'Cloudflare', host: '1.1.1.1', group: 'public' },
];

let events: TimelineEvent[] = [];

function mockApi(status: StatusResponse, ev: TimelineEvent[] = []) {
  events = ev;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const body = url.startsWith('/api/status') ? status : url.startsWith('/api/events') ? events : targets;
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

afterEach(() => vi.restoreAllMocks());

const stats = { count: 60, lost: 0, min: 1, p5: 1, p25: 1, median: 1.2, p75: 1.4, p95: 2, max: 3, avg: 1.3, jitter: 0.2, loss_pct: 0 };

describe('Overview', () => {
  it('shows healthy summary and grouped tiles', async () => {
    mockApi({
      now_ms: 10_000,
      targets: [
        { id: 1, enabled: true, state: 'up', last: { ts_ms: 9_000, ok: true, rtt_ms: 1.1, error: null }, window_s: 60, stats },
        { id: 2, enabled: true, state: 'up', last: { ts_ms: 9_000, ok: true, rtt_ms: 4.5, error: null }, window_s: 60, stats },
      ],
    });
    render(<MemoryRouter><Overview /></MemoryRouter>);
    expect(await screen.findByText('All targets responding')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Gateway' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Internet' })).toBeInTheDocument();
    expect(await screen.findByText('4.50 ms')).toBeInTheDocument();
  });

  it('calls out targets that are down', async () => {
    mockApi({
      now_ms: 10_000,
      targets: [
        { id: 1, enabled: true, state: 'up', last: { ts_ms: 9_000, ok: true, rtt_ms: 1, error: null }, window_s: 60, stats },
        { id: 2, enabled: true, state: 'down', consecutive_failures: 5, last: { ts_ms: 9_000, ok: false, rtt_ms: null, error: 'timeout' }, window_s: 60, stats: { ...stats, lost: 60, loss_pct: 100 } },
      ],
    });
    render(<MemoryRouter><Overview /></MemoryRouter>);
    expect(await screen.findByText('1 target down')).toBeInTheDocument();
    expect(screen.getAllByText('Cloudflare').length).toBeGreaterThan(0);
    expect(await screen.findByText('timeout')).toBeInTheDocument();
  });

  it('lists active incidents', async () => {
    const outage: TimelineEvent = {
      id: 7, kind: 'outage', severity: 'critical', target_id: 2, iface: null, start_ms: Date.now() - 90_000,
      end_ms: null, duration_ms: null, open: true, title: 'Cloudflare unreachable', details: {},
    };
    mockApi({ now_ms: 10_000, targets: [] }, [outage]);
    render(<MemoryRouter><Overview /></MemoryRouter>);
    const region = await screen.findByRole('region', { name: 'Active incidents' });
    expect(region).toHaveTextContent('Cloudflare unreachable');
    expect(region).toHaveTextContent(/ongoing · 1 min/);
  });
  it('renames a target by clicking its name', async () => {
    const puts: { url: string; body: Record<string, unknown> }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body));
        puts.push({ url, body });
        return new Response(JSON.stringify({ ...targets[0], ...body }), { status: 200 });
      }
      const body = url.startsWith('/api/status') ? { now_ms: 1, targets: [] } : url.startsWith('/api/events') ? [] : targets;
      return new Response(JSON.stringify(body), { status: 200 });
    });
    render(<MemoryRouter><Overview /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /Gateway/ }));
    const box = screen.getByRole('textbox', { name: 'New name for Gateway' });
    fireEvent.change(box, { target: { value: 'Bell Home Hub' } });
    fireEvent.submit(box);
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.url).toBe('/api/targets/1');
    expect(puts[0]!.body).toMatchObject({ name: 'Bell Home Hub', host: '192.168.2.1', kind: 'icmp' });
    expect(screen.getByRole('link', { name: 'Open Cloudflare' })).toHaveAttribute('href', '/targets/2');
  });
});
