import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Exposure } from './Exposure';

afterEach(() => vi.restoreAllMocks());

const summary = {
  latest: { id: 1, ts_ms: 1_760_000_000_000, trigger: 'manual', status: 'ok', hosts: 2, open_ports: 3 },
  running: false,
  progress: null,
  nightly: true,
  hub_configured: true,
  hub_read: true,
  hub: { upnp_enabled: true, dmz_host: null, dmz_known: true },
  forwards: [
    { enabled: true, protocol: 'TCP', external_port: 32400, external_port_end: null, internal_ip: '192.168.2.15', internal_port: 32400, description: 'Plex', origin: 'manual', device_id: 5, device_label: 'TrueNAS' },
    { enabled: true, protocol: 'UDP', external_port: 3074, external_port_end: null, internal_ip: '192.168.2.40', internal_port: 3074, description: null, origin: 'upnp', device_id: null, device_label: null },
  ],
  services: [
    { device_id: 9, port: 23, first_seen_ms: 1, last_seen_ms: 1, banner: null, service: 'Telnet', risk: 'high', advice: 'Turn it off.', device_label: 'Garage camera', ip: '192.168.2.30' },
    { device_id: 5, port: 445, first_seen_ms: 1, last_seen_ms: 1, banner: null, service: 'SMB', risk: 'high', advice: 'x', device_label: 'TrueNAS', ip: '192.168.2.15' },
  ],
  findings: [{ severity: 'warning', title: 'Telnet (23) is open on Garage camera', advice: 'Turn it off.', device_id: 9 }],
};

describe('Exposure', () => {
  it('shows findings, port forwards and services, and starts a scan', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/api/exposure/scan') return new Response(JSON.stringify({ started: true }), { status: 202 });
      return new Response(JSON.stringify(summary), { status: 200 });
    });
    render(<MemoryRouter><Exposure /></MemoryRouter>);
    expect(await screen.findByText('Telnet (23) is open on Garage camera')).toBeInTheDocument();
    const internet = screen.getByRole('region', { name: 'Reachable from the internet' });
    expect(within(internet).getByText('TrueNAS')).toBeInTheDocument();
    expect(within(internet).getByText('the device (UPnP)')).toBeInTheDocument();
    expect(within(internet).getByText(/UPnP is on/)).toBeInTheDocument();
    const lan = screen.getByRole('region', { name: 'Open services on the LAN' });
    expect(within(lan).getByText('Garage camera')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Scan now' }));
    await vi.waitFor(() => expect(f).toHaveBeenCalledWith('/api/exposure/scan', expect.anything()));
  });
});
