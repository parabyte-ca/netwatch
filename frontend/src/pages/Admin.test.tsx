import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SettingField } from '../lib/api';
import { Admin } from './Admin';

afterEach(() => vi.restoreAllMocks());

const f = (over: Partial<SettingField>): SettingField => ({
  name: 'down_after', group: 'detection', group_label: 'Outage detection', label: 'Failures before down', help: '',
  unit: 'probes', live: true, type: 'int', value: 3, default: 3, overridden: false, min: 1, max: 100, ...over,
});

const fields = [
  f({}),
  f({ name: 'path_test_interval_min', group: 'path_tests', group_label: 'Path tests', label: 'Scheduled path tests', live: false, value: 15, default: 15, unit: 'min' }),
  f({ name: 'raw_retention_days', group: 'retention', group_label: 'Data retention', label: 'Raw samples', value: 10, default: 7, overridden: true, unit: 'days' }),
];

function mockApi(calls: { url: string; method: string; body?: unknown }[]) {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body });
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url === '/api/admin/settings' && method === 'PUT') {
      const { changes } = JSON.parse(String(init?.body));
      const pending = 'path_test_interval_min' in changes ? ['path_test_interval_min'] : [];
      return json({ fields, restart_pending: pending });
    }
    if (url === '/api/admin/settings') return json({ fields, restart_pending: [] });
    if (url === '/api/admin/sessions') {
      return json([
        { id: 'a', username: 'admin', created_ms: 1, last_seen_ms: Date.now(), expires_ms: 2, ip: '192.168.2.20', user_agent: 'Firefox', current: true },
        { id: 'b', username: 'admin', created_ms: 1, last_seen_ms: Date.now(), expires_ms: 2, ip: '192.168.2.50', user_agent: 'Phone', current: false },
      ]);
    }
    if (url.startsWith('/api/admin/sessions/b')) return new Response(null, { status: 204 });
    if (url === '/api/admin/backups') {
      return json({ backups: [{ name: 'netwatch-20261017-v0.5.0.db', bytes: 1048576, modified: 1792270000 }], restore_pending: false, database_bytes: 2097152 });
    }
    if (url === '/api/admin/security-log') return json([]);
    if (url === '/api/admin/deco/test') return json({ ok: false, error: 'wrong owner password (9 attempts left before lockout)' });
    if (url === '/api/admin/deco') {
      const saved = method === 'PUT' ? JSON.parse(String(init?.body)) : { host: '', enabled: false };
      return json({ host: saved.host, enabled: saved.enabled, password_set: method === 'PUT', last_poll_ms: null, last_ok_ms: null,
        error: null, decos: [], clients: 0, suggestions: [{ ip: '192.168.2.10', label: 'Deco Living Room' }] });
    }
    if (url === '/api/admin/restart') return json({ restarting: true }, 202);
    return json({});
  });
}

describe('Admin', () => {
  it('edits settings, resets one, and offers a restart when needed', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    mockApi(calls);
    render(<MemoryRouter><Admin /></MemoryRouter>);
    const input = await screen.findByLabelText(/Failures before down/);
    expect(screen.getAllByText('restart')).toHaveLength(1);
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText(/Scheduled path tests/), { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save (2)' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.method === 'PUT')!;
    expect(JSON.parse(String(put.body))).toEqual({ changes: { down_after: 5, path_test_interval_min: 30 } });
    expect(await screen.findByText(/Restart NetWatch to apply: Scheduled path tests/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'reset to default' }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === 'PUT').at(-1)?.body).toBe(JSON.stringify({ changes: { raw_retention_days: null } })),
    );
  });

  it('lists sessions and backups with their actions', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    mockApi(calls);
    render(<MemoryRouter><Admin /></MemoryRouter>);
    const sessions = await screen.findByRole('region', { name: 'Signed-in sessions' });
    expect(await within(sessions).findByText('this browser')).toBeInTheDocument();
    const signOut = within(sessions).getAllByRole('button', { name: 'Sign out' });
    expect(signOut).toHaveLength(1);
    fireEvent.click(signOut[0]!);
    await waitFor(() => expect(calls.some((c) => c.url === '/api/admin/sessions/b' && c.method === 'DELETE')).toBe(true));

    const backups = screen.getByRole('region', { name: 'Backup and restore' });
    expect(await within(backups).findByText('netwatch-20261017-v0.5.0.db')).toBeInTheDocument();
    expect(within(backups).getByText(/Database size 2\.0 MB/)).toBeInTheDocument();
    expect(within(backups).getByRole('link', { name: 'Download' }).getAttribute('href')).toBe(
      '/api/admin/backups/netwatch-20261017-v0.5.0.db',
    );
  });

  it('connects the Deco mesh without echoing the password', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    mockApi(calls);
    render(<MemoryRouter><Admin /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '192.168.2.10' }));
    fireEvent.change(screen.getByLabelText('Owner password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText(/Test failed: wrong owner password/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Poll every 30 s'));
    fireEvent.click(within(screen.getByRole('region', { name: 'Deco mesh' })).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/admin/deco' && c.method === 'PUT')).toBe(true));
    const put = calls.find((c) => c.url === '/api/admin/deco' && c.method === 'PUT')!;
    expect(JSON.parse(String(put.body))).toEqual({ host: '192.168.2.10', enabled: true, password: 'pw' });
    expect(await screen.findByText(/Owner password \(saved\)/)).toBeInTheDocument();
    expect(screen.getByLabelText('Owner password')).toHaveValue('');
  });
});
