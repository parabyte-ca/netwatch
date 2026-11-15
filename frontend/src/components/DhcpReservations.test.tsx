import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AdGuardDhcp } from '../lib/api';
import { DeviceReservation, ReservationList, ReserveCurrentButton, hostnameFor } from './DhcpReservations';

afterEach(() => vi.restoreAllMocks());

const v4 = { gateway_ip: '192.168.2.1', subnet_mask: '255.255.255.0', range_start: '192.168.2.100', range_end: '192.168.2.200' };
const dhcp = (over: Partial<AdGuardDhcp> = {}): AdGuardDhcp => ({
  available: true, enabled: true, v4,
  reservations: [{ mac: '00:11:22:00:00:70', ip: '192.168.2.20', hostname: 'nas', static: true, by_netwatch: false, device: { id: 7, label: 'TrueNAS' } }],
  leases: [{ mac: '00:11:22:00:00:50', ip: '192.168.2.150', hostname: 'pixel-9', static: false, by_netwatch: false, device: { id: 5, label: "Sam's Pixel" } }],
  ...over,
});

function mock(state: AdGuardDhcp | 'off') {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url === '/api/adguard/actions/dhcp') return state === 'off' ? json({ detail: 'AdGuard Home actions are turned off' }, 403) : json(state);
    if (url === '/api/adguard/actions/dhcp/reservations' && method === 'POST') {
      return json({ reservation: { mac: '00:11:22:00:00:50', ip: '192.168.2.150', hostname: 'sam-s-pixel', changed_from: null, private_mac: false } });
    }
    if (url === '/api/adguard/actions/dhcp/reservations/bulk') {
      return json({ results: [{ id: 5, label: "Sam's Pixel", ok: true, ip: '192.168.2.150' }, { id: 9, label: 'Old phone', ok: false, error: 'no current DHCP lease from AdGuard Home' }] });
    }
    return json({ removed: {} });
  });
  return calls;
}

const pixel = { id: 5, label: "Sam's Pixel", mac: '00:11:22:00:00:50', macs: ['00:11:22:00:00:50'], last_ip: '192.168.2.150', randomised: false };

describe('DHCP reservations', () => {
  it('makes AdGuard Home hostnames from device names', () => {
    expect(hostnameFor("Sam's Pixel 9!")).toBe('sam-s-pixel-9');
  });

  it('shows nothing when actions are off', async () => {
    mock('off');
    const { container } = render(<MemoryRouter><DeviceReservation d={pixel} /><ReservationList /></MemoryRouter>);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('reserves a device\'s current address after confirming', async () => {
    const calls = mock(dhcp());
    render(<MemoryRouter><DeviceReservation d={pixel} /></MemoryRouter>);
    const card = await screen.findByRole('region', { name: 'DHCP reservation' });
    expect(card).toHaveTextContent('No reservation');
    fireEvent.click(within(card).getByRole('button', { name: 'Reserve this address' }));
    expect(within(card).getByLabelText('Reserved IP address')).toHaveValue('192.168.2.150');
    expect(within(card).getByLabelText('Reservation name')).toHaveValue('sam-s-pixel');
    fireEvent.click(within(card).getByRole('button', { name: 'Reserve' }));
    const dlg = screen.getByRole('dialog', { name: "Reserve 192.168.2.150 for Sam's Pixel?" });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Reserve' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/dhcp/reservations', method: 'POST',
      body: { mac: '00:11:22:00:00:50', ip: '192.168.2.150', hostname: 'sam-s-pixel' } }));
    expect(await within(card).findByText('Reserved 192.168.2.150.')).toBeInTheDocument();
  });

  it('warns about private Wi-Fi addresses', async () => {
    mock(dhcp());
    render(<MemoryRouter><DeviceReservation d={{ ...pixel, mac: 'da:11:22:00:00:50', macs: ['da:11:22:00:00:50'] }} /></MemoryRouter>);
    expect(await screen.findByText(/private \(randomised\) Wi-Fi address/)).toBeInTheDocument();
  });

  it('lists reservations and removes one made in AdGuard Home after a warning', async () => {
    const calls = mock(dhcp());
    render(<MemoryRouter><ReservationList /></MemoryRouter>);
    const list = await screen.findByRole('region', { name: 'DHCP reservations' });
    expect(within(list).getByRole('link', { name: 'TrueNAS' })).toHaveAttribute('href', '/devices/7');
    fireEvent.click(within(list).getByRole('button', { name: 'Remove' }));
    const dlg = screen.getByRole('dialog', { name: 'Remove the reservation of 192.168.2.20 for TrueNAS?' });
    expect(dlg).toHaveTextContent('made in AdGuard Home, not NetWatch');
    fireEvent.click(within(dlg).getByRole('button', { name: 'Remove reservation' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/dhcp/reservations', method: 'DELETE',
      body: { mac: '00:11:22:00:00:70' } }));
  });

  it('reserves the current addresses of selected devices and reports each', async () => {
    const calls = mock(dhcp());
    const onDone = vi.fn(async () => {});
    render(<MemoryRouter><ReserveCurrentButton ids={[5, 9]} onDone={onDone} /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Reserve current addresses' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Reserve' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/dhcp/reservations/bulk', method: 'POST', body: { device_ids: [5, 9] } }));
    expect(await screen.findByRole('status')).toHaveTextContent('Reserved 1 · Old phone: no current DHCP lease from AdGuard Home');
    expect(onDone).toHaveBeenCalled();
  });
});
