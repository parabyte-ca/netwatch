import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AdGuardProfiles } from '../lib/api';
import { DeviceProfile, GroupProfileSelect, ProfileList } from './AdGuardProfile';

afterEach(() => vi.restoreAllMocks());

const state = (over: Partial<AdGuardProfiles> = {}): AdGuardProfiles => ({
  profiles: { devices: {}, groups: { '3': 'strict' } },
  clients: { 'group:3': 'NetWatch: Kids' },
  conflicts: [{ device_id: 9, label: 'Living room TV', mac: '00:11:22:00:00:99', client: 'My TV', profile: 'strict' }],
  last_sync_ms: 1, error: null,
  managed: [{ key: 'group:3', name: 'NetWatch: Kids', profile: 'strict', label: 'Kids', devices: [{ id: 5, label: "Sam's iPad" }] }],
  ...over,
});

function mock(s: AdGuardProfiles | 'off') {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url === '/api/adguard/actions/profiles') return s === 'off' ? json({ detail: 'turned off' }, 403) : json(s);
    return json({ changes: [], conflicts: [] });
  });
  return calls;
}

describe('AdGuard Home profiles', () => {
  it('hides everything when actions are off', async () => {
    mock('off');
    const { container } = render(<MemoryRouter><DeviceProfile d={{ id: 5, label: 'iPad', group: null }} /><ProfileList /></MemoryRouter>);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the group setting on a device and sets its own after confirming', async () => {
    const calls = mock(state());
    render(<MemoryRouter><DeviceProfile d={{ id: 5, label: "Sam's iPad", group: { id: 3, name: 'Kids' } }} /></MemoryRouter>);
    const sel = await screen.findByLabelText('AdGuard Home profile');
    expect(sel).toHaveValue('default');
    expect(within(sel).getByRole('option', { name: 'Use group: Strict' })).toBeInTheDocument();
    fireEvent.change(sel, { target: { value: 'no_blocking' } });
    const dlg = screen.getByRole('dialog', { name: "Set Sam's iPad to No blocking?" });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/profiles/device/5', method: 'PUT', body: { profile: 'no_blocking' } }));
  });

  it('explains a device that is already in your own client', async () => {
    mock(state());
    render(<MemoryRouter><DeviceProfile d={{ id: 9, label: 'Living room TV', group: { id: 3, name: 'Kids' } }} /></MemoryRouter>);
    expect(await screen.findByText(/already in your AdGuard Home client "My TV"/)).toBeInTheDocument();
  });

  it('sets a group profile from the group manager', async () => {
    const calls = mock(state());
    const ask = vi.fn();
    render(<GroupProfileSelect group={{ id: 4, name: 'Teens' }} profiles={state()} onChanged={async () => {}} ask={ask} />);
    fireEvent.change(screen.getByLabelText('AdGuard Home profile for Teens'), { target: { value: 'strict' } });
    expect(ask).toHaveBeenCalledTimes(1);
    const confirm = ask.mock.calls[0]![0];
    expect(confirm.title).toBe('Set the Teens group to Strict?');
    await confirm.run();
    expect(calls).toContainEqual({ url: '/api/adguard/actions/profiles/group/4', method: 'PUT', body: { profile: 'strict' } });
  });

  it('lists the clients NetWatch keeps and the conflicts', async () => {
    mock(state());
    render(<MemoryRouter><ProfileList /></MemoryRouter>);
    const list = await screen.findByRole('region', { name: 'Per-device and per-group settings' });
    expect(list).toHaveTextContent('NetWatch: Kids');
    expect(within(list).getByRole('link', { name: "Sam's iPad" })).toHaveAttribute('href', '/devices/5');
    expect(list).toHaveTextContent('Living room TV is already in your AdGuard Home client "My TV"');
  });
});
