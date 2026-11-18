import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ServersCard } from './ServersCard';

afterEach(() => vi.restoreAllMocks());

interface Call { url: string; method: string; body?: string }

const nas = { id: 1, name: 'TrueNAS', host: '192.168.2.5', port: 2222, username: 'truenas_admin', notes: 'main box',
  ssh_url: 'ssh://truenas_admin@192.168.2.5:2222', ssh_command: 'ssh truenas_admin@192.168.2.5 -p 2222' };

function mock(initial = [nas], failPost = false) {
  let list = [...initial];
  const calls: Call[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body as string | undefined });
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url === '/api/admin/servers/suggestions') {
      return json({ suggestions: [{ host: '192.168.2.53', label: 'AdGuard Home' }, { host: '192.168.2.5', label: 'NAS' }] });
    }
    if (url === '/api/admin/servers' && method === 'POST') {
      if (failPost) return json({ detail: 'a server with that name already exists' }, 422);
      const b = JSON.parse(String(init?.body));
      const s = { ...b, id: 2, ssh_url: `ssh://${b.username}@${b.host}`, ssh_command: `ssh ${b.username}@${b.host}` };
      list = [...list, s];
      return json(s, 201);
    }
    if (url === '/api/admin/servers') return json({ servers: list });
    if (/^\/api\/admin\/servers\/\d+$/.test(url) && method === 'PUT') return json({ ...list[0], ...JSON.parse(String(init?.body)) });
    if (/^\/api\/admin\/servers\/\d+$/.test(url) && method === 'DELETE') {
      list = [];
      return json({ removed: 'TrueNAS' });
    }
    return json({});
  });
  return calls;
}

describe('ServersCard', () => {
  it('says so when there are no servers, and that NetWatch keeps no login', async () => {
    mock([]);
    render(<ServersCard />);
    expect(await screen.findByText('No servers saved yet.')).toBeTruthy();
    expect(screen.getByText(/NetWatch stores no login and has no terminal of its own/)).toBeTruthy();
  });

  it('lists a server with the SSH app link and the command to copy', async () => {
    mock();
    const write = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: write } });
    render(<ServersCard />);
    const item = within(await screen.findByRole('list', { name: 'Saved servers' }));
    expect(item.getByText('truenas_admin@192.168.2.5:2222')).toBeTruthy();
    expect(item.getByRole('link', { name: 'Open in SSH app' }).getAttribute('href')).toBe(nas.ssh_url);
    fireEvent.click(item.getByRole('button', { name: 'Copy command' }));
    await waitFor(() => expect(write).toHaveBeenCalledWith(nas.ssh_command));
    expect(await screen.findByRole('status')).toHaveTextContent(`Copied: ${nas.ssh_command}`);
  });

  it('shows the command to copy by hand when the clipboard is blocked', async () => {
    mock();
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
    render(<ServersCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Copy command' }));
    expect(await screen.findByRole('status')).toHaveTextContent(`Copy this: ${nas.ssh_command}`);
  });

  it('adds a server, filling the name from the address suggestion', async () => {
    const calls = mock([]);
    render(<ServersCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a server' }));
    const add = screen.getByRole('button', { name: 'Add server' });
    expect((add as HTMLButtonElement).disabled).toBe(true); // needs a name, address and user
    fireEvent.change(screen.getByLabelText('Server address'), { target: { value: '192.168.2.53' } });
    await waitFor(() => expect(screen.getByLabelText('Server name')).toHaveValue('AdGuard Home'));
    fireEvent.change(screen.getByLabelText('Server user'), { target: { value: 'root' } });
    fireEvent.change(screen.getByLabelText('Server port'), { target: { value: '22a' } });
    expect(screen.getByLabelText('Server port')).toHaveValue('22'); // digits only
    fireEvent.click(add);
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    expect(JSON.parse(calls.find((c) => c.method === 'POST')!.body!)).toEqual(
      { name: 'AdGuard Home', host: '192.168.2.53', port: 22, username: 'root', notes: null },
    );
    expect(await screen.findByRole('link', { name: 'Open in SSH app' })).toBeTruthy();
  });

  it('shows the reason when a server cannot be saved', async () => {
    mock([], true);
    render(<ServersCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a server' }));
    fireEvent.change(screen.getByLabelText('Server name'), { target: { value: 'TrueNAS' } });
    fireEvent.change(screen.getByLabelText('Server address'), { target: { value: '192.168.2.5' } });
    fireEvent.change(screen.getByLabelText('Server user'), { target: { value: 'admin' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add server' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('a server with that name already exists');
  });

  it('edits a server', async () => {
    const calls = mock();
    render(<ServersCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Server port')).toHaveValue('2222');
    fireEvent.change(screen.getByLabelText('Server port'), { target: { value: '22' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(put.url).toBe('/api/admin/servers/1');
    expect(JSON.parse(put.body!)).toMatchObject({ port: 22, username: 'truenas_admin', notes: 'main box' });
  });

  it('removes a server only after the confirmation', async () => {
    const calls = mock();
    render(<ServersCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    expect(screen.getByRole('dialog')).toHaveTextContent('Nothing changes on the server');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    expect(await screen.findByText('No servers saved yet.')).toBeTruthy();
  });
});
