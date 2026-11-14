import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AdGuardSummary } from '../lib/api';
import { Dns } from './Dns';

// uPlot needs a real browser (matchMedia, canvas); the chart itself is not under test here.
vi.mock('uplot', () => ({ default: class { setSize() {} destroy() {} } }));

afterEach(() => vi.restoreAllMocks());

const now = Date.now();
const base: AdGuardSummary = {
  host: '192.168.2.53', port: 80, https: false, username: 'netwatch', enabled: true, password_set: true,
  last_poll_ms: now, last_ok_ms: now, error: null, paused: null,
  status: { version: 'v0.107.55', protection: true },
  latest: { ts_ms: now, reachable: true, protection: true, queries: 40, blocked: 4, avg_ms: 18.2, p95_ms: 60, source: 'querylog', details: {} },
  hours: 24, series: [{ ts_ms: now - 60_000, reachable: true, queries: 40, blocked: 4, avg_ms: 18.2, p95_ms: 60 }], queries: 1200, blocked_pct: 12.5, failed_pct: 0.4,
  upstreams: [
    { name: 'tls://1.1.1.1:853', queries: 900, failed: 1, failed_pct: 0.1, avg_ms: 21.4 },
    { name: 'https://dns.quad9.net/dns-query', queries: 100, failed: 30, failed_pct: 30, avg_ms: 85 },
  ],
  top_clients: [
    { client: '192.168.2.20', queries: 700, device_id: 4, label: 'Laptop' },
    { client: '192.168.2.99', queries: 50, device_id: null, label: null },
  ],
  dhcp: { available: true, enabled: true, leases: 14 },
  top_domains: [{ domain: 'github.com', queries: 320 }],
  top_blocked: [{ domain: 'ads.example', queries: 90 }],
  unknown_clients: [{ client: '192.168.2.199', queries: 12 }],
};

function mock(summary: AdGuardSummary) {
  const urls: string[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    urls.push(String(input));
    return new Response(JSON.stringify(summary), { status: 200 });
  });
  return urls;
}

describe('Dns', () => {
  it('shows totals, upstream health and the busiest clients', async () => {
    const urls = mock(base);
    render(<MemoryRouter><Dns /></MemoryRouter>);
    expect(await screen.findByText('12.5%')).toBeInTheDocument();
    expect(screen.getByText('1,200')).toBeInTheDocument();
    expect(screen.getByText('18 ms')).toBeInTheDocument();
    const ups = screen.getByRole('region', { name: 'Upstream resolvers' });
    expect(within(ups).getByText('30%')).toHaveClass('text-rose-300');
    expect(within(ups).getByText('0.1%')).toHaveClass('text-emerald-300');
    const clients = screen.getByRole('region', { name: 'Busiest clients' });
    expect(within(clients).getByRole('link', { name: 'Laptop' })).toHaveAttribute('href', '/devices/4');
    expect(within(clients).getByText('Unknown device')).toBeInTheDocument();
    expect(screen.getByText(/AdGuard Home is the DHCP server \(14 leases\)/)).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Top domains' })).getByText('github.com')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Top blocked domains' })).getByText('ads.example')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Unknown clients' })).getByText('192.168.2.199')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '7 days' }));
    await waitFor(() => expect(urls).toContain('/api/adguard?hours=168'));
  });

  it('warns when AdGuard Home is down or protection is off', async () => {
    mock({ ...base, error: 'cannot reach AdGuard Home (ConnectError)', latest: { ...base.latest!, reachable: false, protection: null } });
    render(<MemoryRouter><Dns /></MemoryRouter>);
    expect(await screen.findByText(/AdGuard Home is not answering/)).toBeInTheDocument();
  });

  it('points to Admin when AdGuard Home is not connected', async () => {
    mock({ ...base, host: '', password_set: false, latest: null, status: null, upstreams: [], top_clients: [] });
    render(<MemoryRouter><Dns /></MemoryRouter>);
    expect(await screen.findByRole('link', { name: 'Admin → AdGuard Home' })).toHaveAttribute('href', '/admin');
    expect(screen.queryByRole('region', { name: 'Upstream resolvers' })).not.toBeInTheDocument();
  });
});
