import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeviceGroup, DeviceRow } from '../lib/api';
import { Devices } from './Devices';

afterEach(() => vi.restoreAllMocks());

const now = Date.now();
const kids: DeviceGroup = { id: 3, name: 'Kids', colour: 'rose', icon: 'child', sort: 1, created_ms: 1, devices: 1 };

function device(id: number, over: Partial<DeviceRow>): DeviceRow {
  return {
    id, mac: `aa:00:00:00:00:0${id}`, vendor: null, randomised: false, name: null, hostname: null, mdns_name: null,
    model: null, type: 'unknown', type_auto: 'unknown', type_reason: null, type_override: null, merged_into: null,
    target_id: null, first_seen_ms: now, last_seen_ms: now, last_ip: `192.168.2.${id}`, notes: null, details: {},
    label: `Device ${id}`, online: true, macs: [`aa:00:00:00:00:0${id}`], group: null, group_id: null, tags: [], ...over,
  };
}

function mock() {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
    if (url === '/api/device-groups') return json({ groups: [kids], colours: ['rose', 'sky'], icons: ['child', 'star'] });
    if (url === '/api/device-groups/suggestions') {
      return json([{ name: 'Smart home', group_id: null, colour: 'emerald', icon: 'plug',
        devices: [{ id: 2, label: 'Kasa plug', type: 'iot' }, { id: 4, label: 'Doorbell', type: 'camera' }] }]);
    }
    if (url.startsWith('/api/discovery')) return new Response('null', { status: 200 });
    if (url.startsWith('/api/devices') && method === 'GET') {
      return json({ now_ms: now, devices: [
        device(1, { label: 'Sam iPad', type: 'tablet', group: { id: 3, name: 'Kids', colour: 'rose', icon: 'child' }, group_id: 3, tags: ['upstairs'] }),
        device(2, { label: 'Kasa plug', type: 'iot' }),
        device(4, { label: 'Doorbell', type: 'camera', tags: ['outside'] }),
      ] });
    }
    return json({ updated: 1 });
  });
  return calls;
}

describe('Devices: groups and tags', () => {
  it('shows groups and tags, filters by them, and assigns in bulk', async () => {
    const calls = mock();
    render(<MemoryRouter><Devices /></MemoryRouter>);
    expect(await screen.findByText('Sam iPad')).toBeInTheDocument();
    expect(screen.getAllByText('Kids').length).toBeGreaterThan(0);
    expect(screen.getAllByText('#upstairs').length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('Group'), { target: { value: '3' } });
    expect(screen.queryByText('Kasa plug', { selector: 'a' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Group'), { target: { value: '0' } });
    expect(screen.queryByText('Sam iPad')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Tag filter'), { target: { value: 'outside' } });
    expect(screen.getByText('Doorbell', { selector: 'a' })).toBeInTheDocument();
    expect(screen.queryByText('Kasa plug', { selector: 'a' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Tag filter'), { target: { value: '' } });
    // Bulk: tick two, put them in Kids, tag them.
    fireEvent.click(screen.getByLabelText('Select Kasa plug'));
    fireEvent.click(screen.getByLabelText('Select Doorbell'));
    const bar = screen.getByRole('toolbar', { name: 'Bulk actions' });
    expect(bar).toHaveTextContent('2 selected');
    fireEvent.change(within(bar).getByLabelText('Set group'), { target: { value: '3' } });
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/devices/bulk', method: 'POST', body: { ids: [2, 4], group_id: 3 } }));
    fireEvent.change(within(bar).getByLabelText('Tag'), { target: { value: 'garage' } });
    fireEvent.click(within(bar).getByRole('button', { name: 'Add tag' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/devices/bulk', method: 'POST', body: { ids: [2, 4], add_tags: ['garage'] } }));
  });

  it('accepts a suggestion after unticking a device', async () => {
    const calls = mock();
    render(<MemoryRouter><Devices /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /2 look like Smart home/ }));
    const panel = screen.getByRole('region', { name: 'Group suggestions' });
    fireEvent.click(within(panel).getByLabelText(/Doorbell/));
    fireEvent.click(within(panel).getByRole('button', { name: 'Add 1 to Smart home' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/device-groups/suggestions/accept', method: 'POST',
      body: { group_name: 'Smart home', device_ids: [2] } }));
  });

  it('creates and deletes groups in the manager, with a confirmation', async () => {
    const calls = mock();
    render(<MemoryRouter><Devices /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Groups…' }));
    const dlg = screen.getByRole('dialog', { name: 'Manage groups' });
    fireEvent.change(within(dlg).getByLabelText('New group name'), { target: { value: 'Smart home' } });
    fireEvent.click(within(within(dlg).getByRole('radiogroup', { name: 'Colour' })).getByLabelText('sky'));
    fireEvent.click(within(dlg).getByRole('button', { name: 'Create group' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/device-groups', method: 'POST',
      body: { name: 'Smart home', colour: 'sky', icon: 'star' } }));
    fireEvent.click(within(dlg).getByRole('button', { name: 'Delete' }));
    const confirm = screen.getByRole('dialog', { name: 'Delete the group Kids?' });
    expect(confirm).toHaveTextContent('Its 1 device will be ungrouped');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete group' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/device-groups/3', method: 'DELETE', body: undefined }));
  });
});
