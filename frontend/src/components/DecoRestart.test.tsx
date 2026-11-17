import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DecoRestartButton, restartConfirm } from './DecoRestart';
import { CauseList } from './CauseList';
import type { Cause } from '../lib/api';

afterEach(() => vi.restoreAllMocks());

const units = [
  { mac: 'aa:bb:cc:00:00:01', name: 'Living Room', ip: null, model: null, firmware: null, role: 'main', online: true, backhaul: 'wired' },
  { mac: 'aa:bb:cc:00:00:02', name: 'Office', ip: null, model: null, firmware: null, role: 'satellite', online: true, backhaul: '5 GHz' },
];

function mockDeco(actionsEnabled: boolean, calls: { url: string; method: string }[] = [], decos = units) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? 'GET' });
    if (url === '/api/admin/deco') {
      return new Response(JSON.stringify({ host: '192.168.2.10', enabled: true, password_set: true, decos, clients: 3,
        actions_enabled: actionsEnabled, suggestions: [] }));
    }
    if (url.endsWith('/reboot')) return new Response(JSON.stringify({ started_ms: 1, decos: ['Office'] }));
    return new Response('{}', { status: 404 });
  });
  return calls;
}

describe('restartConfirm', () => {
  it('names what drops for a satellite, the main Deco and the whole network', () => {
    const run = async () => undefined;
    const sat = restartConfirm({ name: 'Office', role: 'satellite' }, run);
    expect(sat.title).toBe('Restart Office?');
    expect(sat.danger).toBe(true);
    const main = restartConfirm({ name: 'Living Room', role: 'main' }, run);
    render(<div>{main.body}</div>);
    expect(screen.getByText(/NetWatch may lose its connection/)).toBeTruthy();
    expect(restartConfirm(null, run).confirmLabel).toBe('Restart all Decos');
  });
});

describe('DecoRestartButton', () => {
  it('stays hidden while Deco actions are off', async () => {
    const calls = mockDeco(false);
    render(<DecoRestartButton mac="aa:bb:cc:00:00:02" name="Office" />);
    await waitFor(() => expect(calls.some((c) => c.url === '/api/admin/deco')).toBe(true));
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('stays hidden when that Deco is offline', async () => {
    const calls = mockDeco(true, [], [{ ...units[1]!, online: false }] as typeof units);
    render(<DecoRestartButton mac="aa:bb:cc:00:00:02" name="Office" />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('asks first, and restarts only after the confirmation', async () => {
    const calls = mockDeco(true);
    render(<DecoRestartButton mac="aa:bb:cc:00:00:02" name="Office" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Restart Office' }));
    expect(calls.some((c) => c.url.endsWith('/reboot'))).toBe(false);
    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Restart Office' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/admin/deco/aa%3Abb%3Acc%3A00%3A00%3A02/reboot' && c.method === 'POST')).toBe(true));
    expect(await screen.findByText(/Restarting Office/)).toBeTruthy();
  });
});

describe('CauseList', () => {
  const base: Cause = { id: 'deco_shared', title: 'Deco Office', layer: 'mesh', confidence: 85, evidence: [], next_steps: ['Restart that Deco.'] };

  it('offers the restart only on a cause that carries the action', async () => {
    mockDeco(true);
    const { rerender } = render(<CauseList causes={[base]} />);
    expect(screen.queryByRole('button', { name: /Restart/ })).toBeNull();
    rerender(<CauseList causes={[{ ...base, action: { kind: 'deco_reboot', mac: 'aa:bb:cc:00:00:02', name: 'Office' } }]} />);
    expect(await screen.findByRole('button', { name: 'Restart Office' })).toBeTruthy();
  });
});
