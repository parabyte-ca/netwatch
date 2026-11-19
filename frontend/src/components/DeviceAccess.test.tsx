import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DeviceAccess as Access } from '../lib/api';
import { DeviceAccess } from './DeviceAccess';

afterEach(() => vi.restoreAllMocks());

const base: Access = {
  ip: '192.168.2.5',
  scanned: true,
  ssh: { available: true, mode: 'auto', detected: true, banner: 'SSH-2.0-OpenSSH_9.2', user: null, port: 22, ssh_url: null, ssh_command: null },
  web: {
    primary: { port: 443, scheme: 'https', url: 'https://192.168.2.5', label: 'HTTPS' },
    others: [{ port: 8080, scheme: 'http', url: 'http://192.168.2.5:8080', label: 'HTTP (alt)' }],
  },
  admin_url: null,
  scanned_ms: Date.now() - 3 * 3600_000,
};
const withUser: Access = {
  ...base,
  ssh: { ...base.ssh, user: 'alan', ssh_url: 'ssh://alan@192.168.2.5', ssh_command: 'ssh alan@192.168.2.5' },
};

interface Call { url: string; method: string; body?: string }

function mock(initial: Access, onPut?: (b: Record<string, unknown>) => Access, checkResult?: Access | 429) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body as string | undefined });
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url === '/api/devices/7/access' && method === 'PUT') return json(onPut ? onPut(JSON.parse(String(init?.body))) : initial);
    if (url === '/api/devices/7/access/check') {
      return checkResult === 429 ? json({ detail: 'checked a moment ago; wait 10 s' }, 429) : json(checkResult ?? initial);
    }
    if (url === '/api/devices/7/access') return json(initial);
    return json({});
  });
  return calls;
}

describe('DeviceAccess', () => {
  it('opens the admin page in a new tab and lists the other web pages', async () => {
    mock(withUser);
    render(<DeviceAccess id={7} />);
    const open = await screen.findByRole('link', { name: 'Open admin page' });
    expect(open.getAttribute('href')).toBe('https://192.168.2.5');
    expect(open.getAttribute('target')).toBe('_blank');
    expect(open.getAttribute('rel')).toBe('noopener noreferrer');
    const also = screen.getByRole('link', { name: 'HTTP (alt) (8080)' });
    expect(also.getAttribute('href')).toBe('http://192.168.2.5:8080');
    expect(screen.getByText(/Last scanned 3h ago/)).toBeTruthy();
  });

  it('gives the SSH app link and the command to copy', async () => {
    mock(withUser);
    const write = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<DeviceAccess id={7} />);
    expect((await screen.findByRole('link', { name: 'Open in SSH app' })).getAttribute('href')).toBe('ssh://alan@192.168.2.5');
    fireEvent.click(screen.getByRole('button', { name: 'Copy command' }));
    await waitFor(() => expect(write).toHaveBeenCalledWith('ssh alan@192.168.2.5'));
    expect(await screen.findByRole('status')).toHaveTextContent('Copied: ssh alan@192.168.2.5');
  });

  it('shows the command to copy by hand when the clipboard is blocked', async () => {
    mock(withUser);
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('no')) } });
    render(<DeviceAccess id={7} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Copy command' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Copy this: ssh alan@192.168.2.5');
  });

  it('asks for the SSH user first, then offers the link', async () => {
    const calls = mock(base, () => withUser);
    render(<DeviceAccess id={7} />);
    expect(await screen.findByText(/SSH is open \(SSH-2.0-OpenSSH_9.2\)/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Open in SSH app' })).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'Settings' })[0]!); // the one in the heading
    fireEvent.change(screen.getByLabelText('SSH user'), { target: { value: 'alan' } });
    fireEvent.change(screen.getByLabelText('SSH port'), { target: { value: '22x' } });
    expect(screen.getByLabelText('SSH port')).toHaveValue('22'); // digits only
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)).toEqual(
      { ssh_user: 'alan', ssh_port: 22, ssh_mode: 'auto', admin_url: null },
    );
    expect(await screen.findByRole('link', { name: 'Open in SSH app' })).toBeTruthy();
  });

  it('says SSH was not found, and lets you force it on or hide it', async () => {
    const none: Access = { ...base, ssh: { ...base.ssh, available: false, detected: false, banner: null } };
    const calls = mock(none, (b) => ({ ...none, ssh: { ...none.ssh, available: b.ssh_mode === 'on', mode: b.ssh_mode as 'on' } }));
    render(<DeviceAccess id={7} />);
    expect(await screen.findByText('No SSH found at the last scan.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    fireEvent.change(screen.getByLabelText('Show SSH'), { target: { value: 'on' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === 'PUT')!.body!)).toMatchObject({ ssh_mode: 'on' });
    expect(await screen.findByText(/SSH is open/)).toBeTruthy();
  });

  it('says so when SSH is hidden and when there is no web page', async () => {
    mock({ ...base, ssh: { ...base.ssh, mode: 'off', available: false }, web: { primary: null, others: [] } });
    render(<DeviceAccess id={7} />);
    expect(await screen.findByText('SSH is hidden for this device.')).toBeTruthy();
    expect(screen.getByText('No web interface found at the last scan.')).toBeTruthy();
  });

  it('checks now and shows the new result, or the reason it was refused', async () => {
    const fresh: Access = { ...withUser, web: { primary: null, others: [] } };
    const calls = mock(withUser, undefined, fresh);
    render(<DeviceAccess id={7} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/check') && c.method === 'POST')).toBe(true));
    expect(await screen.findByText('No web interface found at the last scan.')).toBeTruthy();
    vi.restoreAllMocks();
    mock(withUser, undefined, 429);
    const { container } = render(<DeviceAccess id={7} />);
    fireEvent.click(await within(container).findByRole('button', { name: 'Check now' }));
    expect(await within(container).findByRole('alert')).toHaveTextContent('checked a moment ago; wait 10 s');
  });

  it('shows nothing for a device without an address', async () => {
    mock({ ...base, ip: null });
    const { container } = render(<DeviceAccess id={7} />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container.querySelector('section')).toBeNull();
  });
});

describe('DeviceAccess for a remote server', () => {
  const remote: Access = {
    ip: 'vps.example.com',
    scanned: false,
    ssh: { available: true, mode: 'auto', detected: false, banner: null, user: null, port: 22, ssh_url: null, ssh_command: null },
    web: { primary: null, others: [] },
    admin_url: null,
    scanned_ms: null,
  };

  function mockRemote(initial: Access, afterSave?: Access) {
    const calls: Call[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method, body: init?.body as string | undefined });
      const body = method === 'PUT' && afterSave ? afterSave : initial;
      return new Response(JSON.stringify(url === '/api/targets/7/access' ? body : {}));
    });
    return calls;
  }

  it('is saved by hand: no scan text, no Check now, an address hint, and Show or Hide only', async () => {
    const calls = mockRemote(remote);
    render(<DeviceAccess id={7} remote />);
    expect(await screen.findByText('No admin page address saved. Add one under Settings.')).toBeTruthy();
    expect(calls[0]!.url).toBe('/api/targets/7/access');
    expect(screen.queryByRole('button', { name: 'Check now' })).toBeNull();
    expect(screen.queryByText(/scanned/i)).toBeNull();
    expect(screen.getByText(/enter the user you log in as/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Settings' })[0]!);
    const options = within(screen.getByLabelText('Show SSH')).getAllByRole('option').map((o) => o.textContent);
    expect(options).toEqual(['Show', 'Hide']);
    expect(screen.getByLabelText('Admin page address').getAttribute('placeholder')).toBe('https://vps.example.com/');
    expect(screen.getByText(/this server's own address/)).toBeTruthy();
  });

  it('saves the user and an admin address to the target, then offers both links', async () => {
    const saved: Access = {
      ...remote,
      ssh: { ...remote.ssh, user: 'ubuntu', port: 2222, ssh_url: 'ssh://ubuntu@vps.example.com:2222', ssh_command: 'ssh ubuntu@vps.example.com -p 2222' },
      web: { primary: { port: null, scheme: 'https', url: 'https://vps.example.com:9443/ui', label: 'Saved address' }, others: [] },
      admin_url: 'https://vps.example.com:9443/ui',
    };
    const calls = mockRemote(remote, saved);
    render(<DeviceAccess id={7} remote />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Settings' }))[0]!);
    fireEvent.change(screen.getByLabelText('SSH user'), { target: { value: 'ubuntu' } });
    fireEvent.change(screen.getByLabelText('SSH port'), { target: { value: '2222' } });
    fireEvent.change(screen.getByLabelText('Admin page address'), { target: { value: 'https://vps.example.com:9443/ui' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(put.url).toBe('/api/targets/7/access');
    expect(JSON.parse(put.body!)).toEqual({ ssh_user: 'ubuntu', ssh_port: 2222, ssh_mode: 'auto', admin_url: 'https://vps.example.com:9443/ui' });
    expect((await screen.findByRole('link', { name: 'Open in SSH app' })).getAttribute('href')).toBe('ssh://ubuntu@vps.example.com:2222');
    expect(screen.getByRole('link', { name: 'Open admin page' }).getAttribute('href')).toBe('https://vps.example.com:9443/ui');
  });
});
