import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeviceGroup, DeviceRow } from '../lib/api';
import { Devices } from './Devices';

afterEach(() => vi.restoreAllMocks());

const now = Date.now();
const kids: DeviceGroup = { id: 3, name: 'Kids', colour: 'rose', icon: 'child', quiet: false, sort: 1, created_ms: 1, devices: 1 };

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
    if (url === '/api/device-groups/stats?hours=24') {
      return json([{ id: 3, name: 'Kids', colour: 'rose', icon: 'child', quiet: false, devices: 1, online: 1, down_bytes: 5e9, up_bytes: 1e8,
        top_talker: 'Sam iPad', outages: 0, alerts: 2, quiet_events: 0, dns: { queries: 3400, blocked_pct: 12.5, failed_pct: 0 } },
        { id: 0, name: 'Ungrouped', colour: 'slate', icon: 'circle', quiet: false, devices: 2, online: 2, down_bytes: 0, up_bytes: 0,
          top_talker: null, outages: 0, alerts: 0, quiet_events: 0, dns: null }]);
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

  it('shows groups at a glance and filters the table when a card is clicked', async () => {
    mock();
    render(<MemoryRouter><Devices /></MemoryRouter>);
    const strip = await screen.findByRole('region', { name: 'Groups at a glance' });
    const card = await within(strip).findByRole('button', { name: /Kids/ });
    expect(card).toHaveTextContent('1/1 online');
    expect(card).toHaveTextContent('3,400 DNS queries, 12.5% blocked');
    expect(card).toHaveTextContent('2 alerts');
    expect(card).toHaveTextContent('Busiest: Sam iPad');
    fireEvent.click(card);
    expect(card).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('Kasa plug', { selector: 'a' })).not.toBeInTheDocument();
    expect(screen.getByText('Sam iPad', { selector: 'a' })).toBeInTheDocument();
  });

  it('makes a group quiet from the manager', async () => {
    const calls = mock();
    render(<MemoryRouter><Devices /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Groups…' }));
    const dlg = screen.getByRole('dialog', { name: 'Manage groups' });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Edit' }));
    fireEvent.click(within(dlg).getAllByLabelText('Quiet')[0] as HTMLElement);
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/device-groups/3', method: 'PATCH',
      body: { name: 'Kids', colour: 'rose', icon: 'child', quiet: true } }));
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
      body: { name: 'Smart home', colour: 'sky', icon: 'star', quiet: false } }));
    fireEvent.click(within(dlg).getByRole('button', { name: 'Delete' }));
    const confirm = screen.getByRole('dialog', { name: 'Delete the group Kids?' });
    expect(confirm).toHaveTextContent('Its 1 device will be ungrouped');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete group' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/device-groups/3', method: 'DELETE', body: undefined }));
  });
});
