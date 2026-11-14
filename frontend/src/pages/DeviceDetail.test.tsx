import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DeviceDetail } from './DeviceDetail';

// uPlot (bandwidth chart) needs a real browser; the chart is not under test here.
vi.mock('uplot', () => ({ default: class { setSize() {} destroy() {} } }));

afterEach(() => vi.restoreAllMocks());

const now = Date.now();
const device = {
  id: 7, mac: 'aa:00:00:00:00:07', vendor: null, randomised: false, name: 'Sam iPad', hostname: null, mdns_name: null,
  model: null, type: 'tablet', type_auto: 'tablet', type_reason: null, type_override: null, merged_into: null, target_id: null,
  first_seen_ms: now, last_seen_ms: now, last_ip: '192.168.2.7', notes: null, details: {}, label: 'Sam iPad', online: true,
  macs: ['aa:00:00:00:00:07'], group: null, group_id: null, tags: ['upstairs'],
};

describe('DeviceDetail: group and tags', () => {
  it('sets the group and adds and removes tags', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
      if (url === '/api/device-groups') return json({ groups: [{ id: 3, name: 'Kids', colour: 'rose', icon: 'child', sort: 1, created_ms: 1, devices: 0 }], colours: [], icons: [] });
      if (url === '/api/device-tags') return json([{ tag: 'upstairs', devices: 1 }, { tag: 'school', devices: 2 }]);
      if (url === '/api/devices/7') return json({ device, members: [device], ips: [], target: null, events: [], services: [] });
      if (url.startsWith('/api/devices?') || url === '/api/devices') return json({ now_ms: now, devices: [device] });
      if (url.includes('/dns')) return json({ configured: false, queries: 0, hours: 24, blocked_pct: null, failed_pct: null, hourly: [], top_domains: [], top_blocked: [] });
      if (url.includes('/traffic')) return json({ hours: 24, series: [], down_bytes: 0, up_bytes: 0 });
      return json({});
    });
    render(<MemoryRouter initialEntries={['/devices/7']}><Routes><Route path="/devices/:id" element={<DeviceDetail />} /></Routes></MemoryRouter>);
    const box = await screen.findByLabelText('Group and tags');
    await waitFor(() => expect(within(box).getByRole('option', { name: 'Kids' })).toBeInTheDocument());
    fireEvent.change(within(box).getByLabelText('Group'), { target: { value: '3' } });
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/devices/7', method: 'PATCH', body: { group_id: 3 } }));
    fireEvent.change(within(box).getByLabelText('Add tag'), { target: { value: 'Back Yard' } });
    fireEvent.submit(within(box).getByLabelText('Add tag'));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/devices/7', method: 'PATCH', body: { tags: ['upstairs', 'back-yard'] } }));
    fireEvent.click(within(box).getByRole('button', { name: 'Remove tag upstairs' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/devices/7', method: 'PATCH', body: { tags: [] } }));
  });
});
