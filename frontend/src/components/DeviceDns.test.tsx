import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeviceDns as Data } from '../lib/api';
import { DeviceDns } from './DeviceDns';

afterEach(() => vi.restoreAllMocks());

const data: Data = {
  configured: true, hours: 24, queries: 1234, blocked_pct: 12.5, failed_pct: 0.4,
  hourly: [{ hour_ms: 1_763_100_000_000, queries: 100, blocked: 10 }, { hour_ms: 1_763_103_600_000, queries: 50, blocked: 0 }],
  top_domains: [{ domain: 'github.com', queries: 300 }],
  top_blocked: [{ domain: 'ads.example', queries: 40 }],
};

function mock(d: Data) {
  const urls: string[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    urls.push(String(input));
    return new Response(JSON.stringify(d), { status: 200 });
  });
  return urls;
}

describe('DeviceDns', () => {
  it('shows counts, hourly bars and top domains, and switches range', async () => {
    const urls = mock(data);
    render(<DeviceDns id={7} />);
    const s = await screen.findByRole('region', { name: 'DNS' });
    expect(within(s).getByText(/1,234 queries, 12.5% blocked, 0.4% failed/)).toBeInTheDocument();
    expect(within(s).getByLabelText('Queries per hour').children).toHaveLength(2);
    expect(within(s).getByText('github.com')).toBeInTheDocument();
    expect(within(s).getByText('ads.example')).toBeInTheDocument();
    fireEvent.click(within(s).getByRole('button', { name: '7 days' }));
    await waitFor(() => expect(urls).toContain('/api/devices/7/dns?hours=168'));
  });

  it('stays hidden when AdGuard Home is not set up', async () => {
    const urls = mock({ ...data, configured: false, queries: 0, hourly: [], top_domains: [], top_blocked: [] });
    const { container } = render(<DeviceDns id={7} />);
    await waitFor(() => expect(urls.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });
});
