import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AdGuardProfiles } from '../lib/api';
import { DeviceProfile, GroupProfileButton, ProfileList, describe as describeSettings, hostOf } from './AdGuardProfile';

afterEach(() => vi.restoreAllMocks());

const strict = { filtering: 'strict' as const, youtube: 'restricted' as const, services: ['tiktok'] };
const services = [{ id: 'youtube', name: 'YouTube' }, { id: 'tiktok', name: 'TikTok' }, { id: 'roblox', name: 'Roblox' }];
const state = (over: Partial<AdGuardProfiles> = {}): AdGuardProfiles => ({
  profiles: { devices: {}, groups: { '3': strict } },
  clients: { 'group:3': 'NetWatch: Kids' },
  conflicts: [{ device_id: 9, label: 'Living room TV', mac: '00:11:22:00:00:99', client: 'My TV', profile: strict }],
  last_sync_ms: 1, error: null,
  managed: [{ key: 'group:3', name: 'NetWatch: Kids', profile: strict, label: 'Kids', devices: [{ id: 5, label: "Sam's iPad" }] }],
  services_all: services,
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

describe('AdGuard Home settings', () => {
  it('describes settings like the security log', () => {
    expect(describeSettings(strict, { tiktok: 'TikTok' })).toBe('Strict, YouTube Restricted Mode, also blocks TikTok');
    expect(describeSettings({ filtering: 'default', youtube: 'default', services: ['roblox'] }, { roblox: 'Roblox' })).toBe('Default filtering, also blocks Roblox');
  });

  it('hides everything when actions are off', async () => {
    mock('off');
    const { container } = render(<MemoryRouter><DeviceProfile d={{ id: 5, label: 'iPad', group: null }} /><ProfileList /></MemoryRouter>);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the group settings on a device and saves its own (YouTube blocked, Roblox) after confirming', async () => {
    const calls = mock(state());
    render(<MemoryRouter><DeviceProfile d={{ id: 5, label: "Sam's iPad", group: { id: 3, name: 'Kids' } }} /></MemoryRouter>);
    const card = await screen.findByRole('region', { name: 'AdGuard Home settings' });
    expect(card).toHaveTextContent('Now: Strict, YouTube Restricted Mode, also blocks TikTok (from the Kids group)');
    fireEvent.change(within(card).getByLabelText('Device YouTube'), { target: { value: 'blocked' } });
    fireEvent.change(within(card).getByLabelText('Device add an app to block'), { target: { value: 'roblox' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }));
    const dlg = screen.getByRole('dialog', { name: "Set Sam's iPad to YouTube blocked, also blocks Roblox?" });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/profiles/device/5', method: 'PUT',
      body: { filtering: 'default', youtube: 'blocked', services: ['roblox'] } }));
  });

  it('says YouTube Allowed also allows its domains, and not for the other choices', async () => {
    mock(state());
    render(<MemoryRouter><DeviceProfile d={{ id: 5, label: "Sam's iPad", group: { id: 3, name: 'Kids' } }} /></MemoryRouter>);
    const card = await screen.findByRole('region', { name: 'AdGuard Home settings' });
    fireEvent.change(within(card).getByLabelText('Device YouTube'), { target: { value: 'allowed' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }));
    const dlg = screen.getByRole('dialog');
    expect(dlg).toHaveTextContent("NetWatch also allows YouTube's own domains for it");
    fireEvent.click(within(dlg).getByRole('button', { name: 'Cancel' }));
    fireEvent.change(within(card).getByLabelText('Device YouTube'), { target: { value: 'blocked' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('dialog')).not.toHaveTextContent("YouTube's own domains");
  });

  it('explains a device that is already in your own client', async () => {
    mock(state());
    render(<MemoryRouter><DeviceProfile d={{ id: 9, label: 'Living room TV', group: { id: 3, name: 'Kids' } }} /></MemoryRouter>);
    expect(await screen.findByText(/already in your AdGuard Home client "My TV"/)).toBeInTheDocument();
  });

  it('edits a group in a dialog from the group manager', async () => {
    const calls = mock(state());
    const ask = vi.fn();
    render(<GroupProfileButton group={{ id: 3, name: 'Kids' }} profiles={state()} onChanged={async () => {}} ask={ask} />);
    fireEvent.click(screen.getByRole('button', { name: /AdGuard Home: Strict/ }));
    const dlg = screen.getByRole('dialog', { name: 'AdGuard Home settings for Kids' });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Stop blocking TikTok' }));
    fireEvent.change(within(dlg).getByLabelText('Kids YouTube'), { target: { value: 'blocked' } });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    const confirm = ask.mock.calls[0]![0];
    expect(confirm.title).toBe('Set the Kids group to Strict, YouTube blocked?');
    await confirm.run();
    expect(calls).toContainEqual({ url: '/api/adguard/actions/profiles/group/3', method: 'PUT',
      body: { filtering: 'strict', youtube: 'blocked', services: [] } });
  });

  it('lists the clients NetWatch keeps with their settings, and the conflicts', async () => {
    mock(state());
    render(<MemoryRouter><ProfileList /></MemoryRouter>);
    const list = await screen.findByRole('region', { name: 'Per-device and per-group settings' });
    expect(list).toHaveTextContent('NetWatch: KidsStrict, YouTube Restricted Mode, also blocks TikTok');
    expect(within(list).getByRole('link', { name: "Sam's iPad" })).toHaveAttribute('href', '/devices/5');
    expect(list).toHaveTextContent('Living room TV is already in your AdGuard Home client "My TV"');
  });
});

describe('always-allowed domains', () => {
  it('turns a pasted address into its site', () => {
    expect(hostOf(' https://Classroom.Google.com/u/0 ')).toBe('classroom.google.com');
    expect(hostOf('school.ca/login')).toBe('school.ca');
    expect(hostOf('school.ca')).toBe('school.ca');
  });

  it("adds an exception for one device, showing its group's exceptions", async () => {
    const calls = mock(state({ allow: { 'group:3': ['khanacademy.org'], 'device:5': ['school.ca'] } }));
    render(<MemoryRouter><DeviceProfile d={{ id: 5, label: "Sam's iPad", group: { id: 3, name: 'Kids' } }} /></MemoryRouter>);
    const box = await screen.findByLabelText('Always allowed');
    expect(within(box).getByText('school.ca')).toBeInTheDocument();
    expect(within(box).getByText('khanacademy.org')).toBeInTheDocument();
    expect(within(box).getByText('from Kids')).toBeInTheDocument();
    fireEvent.change(within(box).getByLabelText("Always allow for Sam's iPad"), { target: { value: 'classroom.google.com' } });
    fireEvent.click(within(box).getByRole('button', { name: 'Allow' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Internet off schedules still apply.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Allow' }));
    await waitFor(() => expect(calls).toContainEqual({
      url: '/api/adguard/actions/profiles/device/5/allow', method: 'PUT', body: { domains: ['school.ca', 'classroom.google.com'] },
    }));
  });

  it("removes one and fills in a domain from the device's DNS card", async () => {
    const calls = mock(state({ allow: { 'device:5': ['school.ca', 'b.ca'] } }));
    render(<MemoryRouter><DeviceProfile d={{ id: 5, label: 'iPad', group: null }} /></MemoryRouter>);
    const box = await screen.findByLabelText('Always allowed');
    window.dispatchEvent(new CustomEvent('netwatch:allow-domain', { detail: { deviceId: 5, domain: 'cdn.school.ca' } }));
    await waitFor(() => expect(within(box).getByLabelText('Always allow for iPad')).toHaveValue('cdn.school.ca'));
    window.dispatchEvent(new CustomEvent('netwatch:allow-domain', { detail: { deviceId: 6, domain: 'other.ca' } }));
    expect(within(box).getByLabelText('Always allow for iPad')).toHaveValue('cdn.school.ca'); // another device's
    fireEvent.click(within(box).getByRole('button', { name: 'Remove b.ca' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(calls).toContainEqual({
      url: '/api/adguard/actions/profiles/device/5/allow', method: 'PUT', body: { domains: ['school.ca'] },
    }));
  });
});
