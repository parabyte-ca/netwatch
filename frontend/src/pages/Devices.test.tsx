import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeviceRow, DiscoveryStatus } from '../lib/api';
import { Devices } from './Devices';

afterEach(() => vi.restoreAllMocks());

const now = Date.now();

function device(id: number, over: Partial<DeviceRow>): DeviceRow {
  return {
    id, mac: `3c:84:6a:00:00:0${id}`, vendor: 'TP-Link', randomised: false, name: null, hostname: null,
    mdns_name: null, model: null, type: 'unknown', type_auto: 'unknown', type_reason: null, type_override: null,
    merged_into: null, target_id: null, first_seen_ms: now - 86_400_000, last_seen_ms: now, last_ip: `192.168.2.${id}`,
    notes: null, details: {}, label: `Device ${id}`, online: true, macs: [`3c:84:6a:00:00:0${id}`], ...over,
  };
}

const status: DiscoveryStatus = {
  interface: 'br0', address: '192.168.2.15/24',
  passive: { enabled: true, running: true, error: null, frames: 10, dropped: 0, packets: { arp: 8, dhcp: 2 }, arp_pps: 0.4 },
  active: { enabled: false, interval_min: 60, rate_pps: 50, networks: ['192.168.2.0/24'], notes: [], scanning: false,
    unavailable: null, last_scan: null },
  gateway: { ip: '192.168.2.1', mac: 'a0:b5:49:00:00:01' },
  dhcp_servers: [
    { ip: '192.168.2.1', mac: 'a0:b5:49:00:00:01', vendor: 'Sagemcom', last_seen_ms: now },
    { ip: '192.168.68.1', mac: '3c:84:6a:00:00:09', vendor: 'TP-Link', last_seen_ms: now },
  ],
  dns_check: null, learning_until_ms: now - 1, devices: { total: 3, online: 2 },
};

describe('Devices', () => {
  it('lists devices, filters, and switches active scanning on', async () => {
    const calls: { url: string; method: string; body?: string }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body as string | undefined });
      if (url.startsWith('/api/discovery')) {
        const enabled = init?.method === 'PUT';
        return new Response(JSON.stringify({ ...status, active: { ...status.active, enabled } }), { status: 200 });
      }
      return new Response(JSON.stringify({
        now_ms: now,
        devices: [
          device(1, { label: 'Deco-X55', type: 'access_point', hostname: 'Deco-X55', target_id: 4 }),
          device(2, { label: 'Office PC', type: 'computer', hostname: 'DESKTOP-1' }),
          device(3, { label: 'Old phone', type: 'phone', online: false, last_seen_ms: now - 3 * 86_400_000, randomised: true,
            vendor: null, macs: ['02:11:22:33:44:55', '06:aa:bb:cc:dd:ee'] }),
        ],
      }), { status: 200 });
    });
    render(<MemoryRouter><Devices /></MemoryRouter>);
    expect(await screen.findByText('Deco-X55')).toBeInTheDocument();
    expect(screen.getByText('monitored')).toBeInTheDocument();
    expect(screen.getByText('DESKTOP-1')).toBeInTheDocument();
    expect(screen.getByText('3d ago')).toBeInTheDocument();
    expect(screen.getByText('+1')).toBeInTheDocument();
    expect(screen.getByText(/more than one!/)).toBeInTheDocument();
    expect(screen.getByText('Listening on br0')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Online only'));
    expect(screen.queryByText('Old phone')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'computer' } });
    const table = screen.getByRole('table');
    expect(within(table).queryByText('Deco-X55')).not.toBeInTheDocument();
    expect(within(table).getByText('Office PC')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: /Scan 192.168.2.0\/24/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.body === '{"active":true}')).toBe(true));
  });

  it('sends the search to the server', async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      urls.push(String(input));
      if (String(input).startsWith('/api/discovery')) return new Response(JSON.stringify(status), { status: 200 });
      return new Response(JSON.stringify({ now_ms: now, devices: [] }), { status: 200 });
    });
    render(<MemoryRouter><Devices /></MemoryRouter>);
    expect(await screen.findByText(/No devices seen yet/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search devices'), { target: { value: 'deco' } });
    await waitFor(() => expect(urls).toContain('/api/devices?q=deco'));
  });
});
