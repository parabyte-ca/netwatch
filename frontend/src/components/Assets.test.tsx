import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AssetsState } from '../lib/api';
import { AssetEditor } from './AssetEditor';
import { ConnectedThrough } from './ConnectedThrough';
import { SwitchTools } from './SwitchTools';

afterEach(() => vi.restoreAllMocks());

const DECO = 'deco:aa:bb:cc:00:00:01';
const base = (over: Partial<AssetsState> = {}): AssetsState => ({
  assets: [
    { id: 1, kind: 'switch', name: 'Switch', uplink: 'hub', notes: null, members: [{ key: 'truenas', label: 'TrueNAS (NetWatch)' }] },
    { id: 2, kind: 'switch', name: 'Office', uplink: 'hub', notes: 'under the desk', members: [] },
  ],
  decos: [{ id: DECO, name: 'Deco Office' }],
  pins: { truenas: 'asset:1' },
  hints: { unclaimed: [{ id: 11, label: 'Lonely NAS' }], decos: [] },
  ...over,
});
const dev = (id: number, label: string, connection?: string, merged_into: number | null = null) => ({
  id, label, merged_into, details: connection ? { connection } : {},
});

type Call = { url: string; method: string; body?: Record<string, unknown> };
function mock(state: AssetsState, calls: Call[] = [], fail?: string) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url.startsWith('/api/devices')) {
      return json({ now_ms: 1, devices: [dev(11, 'Lonely NAS'), dev(12, 'Phone', '5 GHz'), dev(13, 'Camera', 'wired'), dev(14, 'Old', undefined, 11)] });
    }
    if (url.startsWith('/api/assets')) {
      if (method !== 'GET') {
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
        if (fail) return json({ detail: fail }, 422);
      }
      return json(method === 'POST' && url === '/api/assets' ? { ...state, asset: { ...state.assets[1]!, id: 3 } } : state);
    }
    return json({});
  });
  return calls;
}

describe('AssetEditor', () => {
  it('shows the switch, saves a new name and uplink, and unpins a device', async () => {
    const calls = mock(base());
    render(<AssetEditor id={1} onChanged={async () => undefined} />);
    const card = await screen.findByRole('region', { name: 'Switch settings' });
    expect(within(card).getByDisplayValue('Switch')).toBeInTheDocument();
    expect(within(card).getByText('TrueNAS (NetWatch)')).toBeInTheDocument();
    fireEvent.change(within(card).getByDisplayValue('Switch'), { target: { value: 'Rack switch' } });
    fireEvent.change(within(card).getByLabelText('Plugs into'), { target: { value: DECO } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets/1', method: 'PUT', body: { name: 'Rack switch', uplink: DECO, notes: '' } }));
    fireEvent.click(within(card).getByRole('button', { name: 'Unpin TrueNAS (NetWatch)' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets/assign', method: 'POST', body: { nodes: ['truenas'], parent: null } }));
  });

  it('offers wired, unpinned devices (likely ones first), never a Wi-Fi device, and pins the ticked ones', async () => {
    const calls = mock(base());
    render(<AssetEditor id={2} onChanged={async () => undefined} />);
    const card = await screen.findByRole('region', { name: 'Switch settings' });
    fireEvent.click(within(card).getByText('Pin devices behind this switch'));
    const boxes = await within(card).findAllByRole('checkbox');
    expect(boxes.map((b) => b.parentElement!.textContent?.trim())).toEqual(['Lonely NAS', 'Camera']);
    expect(within(card).queryByText('Phone')).toBeNull();
    expect(within(card).queryByText('Old')).toBeNull();
    expect(within(card).getByText(/Probably here/)).toBeInTheDocument();
    fireEvent.click(boxes[0]!);
    fireEvent.click(boxes[1]!);
    fireEvent.click(within(card).getByRole('button', { name: 'Pin 2 devices' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets/assign', method: 'POST', body: { nodes: ['dev:11', 'dev:13'], parent: 'asset:2' } }));
  });

  it('removes a switch only after confirming, and shows a refusal', async () => {
    const calls = mock(base());
    const done = vi.fn(async () => undefined);
    render(<AssetEditor id={2} onChanged={done} />);
    const card = await screen.findByRole('region', { name: 'Switch settings' });
    fireEvent.click(within(card).getByRole('button', { name: 'Remove switch' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toHaveTextContent('moves to The Home Hub');
    fireEvent.click(within(dlg).getByRole('button', { name: 'Remove switch' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets/2', method: 'DELETE', body: undefined }));
    await waitFor(() => expect(done).toHaveBeenCalled());
  });

  it('says why a save was refused', async () => {
    mock(base(), [], 'switches cannot plug into each other in a loop');
    render(<AssetEditor id={2} onChanged={async () => undefined} />);
    const card = await screen.findByRole('region', { name: 'Switch settings' });
    fireEvent.change(within(card).getByLabelText('Plugs into'), { target: { value: 'asset:1' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }));
    expect(await within(card).findByRole('alert')).toHaveTextContent('loop');
  });
});

describe('SwitchTools', () => {
  it('adds a switch that plugs into a Deco', async () => {
    const calls = mock(base());
    const done = vi.fn(async () => undefined);
    render(<SwitchTools onChanged={done} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add switch' }));
    fireEvent.change(screen.getByLabelText('Switch name'), { target: { value: 'Garage' } });
    fireEvent.change(screen.getByLabelText('New switch plugs into'), { target: { value: DECO } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets', method: 'POST', body: { name: 'Garage', uplink: DECO } }));
    await waitFor(() => expect(done).toHaveBeenCalled());
  });

  it('suggests a switch on a Deco with several wired devices, and creates it with them on request', async () => {
    const hints = { unclaimed: [], decos: [{ deco_mac: 'aa:bb:cc:00:00:01', deco: DECO, name: 'Deco Office', devices: [{ id: 21, label: 'TV' }, { id: 22, label: 'Console' }, { id: 23, label: 'Printer' }] }] };
    const calls = mock(base({ hints }));
    render(<SwitchTools onChanged={async () => undefined} />);
    expect(await screen.findByText(/has 3 wired devices/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create a switch on Deco Office with these devices' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets', method: 'POST', body: { name: 'Deco Office switch', uplink: DECO } }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets/assign', method: 'POST', body: { nodes: ['dev:21', 'dev:22', 'dev:23'], parent: 'asset:3' } }));
  });

  it('stays quiet when the reply is not a switch list', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));
    const { container } = render(<SwitchTools onChanged={async () => undefined} />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

describe('ConnectedThrough', () => {
  it('pins a wired device to a switch or a Deco, and back to automatic', async () => {
    const calls = mock(base());
    render(<ConnectedThrough id={13} wifi={false} />);
    const pick = (await screen.findByLabelText('Plugged into')) as HTMLSelectElement;
    expect(pick.value).toBe('');
    fireEvent.change(pick, { target: { value: 'asset:2' } });
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets/assign', method: 'POST', body: { nodes: ['dev:13'], parent: 'asset:2' } }));
    fireEvent.change(pick, { target: { value: '' } });
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/assets/assign', method: 'POST', body: { nodes: ['dev:13'], parent: null } }));
  });

  it('greys out the switches for a Wi-Fi device, and shows a saved pin', async () => {
    mock(base({ pins: { 'dev:12': DECO } }));
    render(<ConnectedThrough id={12} wifi />);
    const pick = (await screen.findByLabelText('Plugged into')) as HTMLSelectElement;
    expect(pick.value).toBe(DECO);
    const sw = [...pick.options].filter((o) => o.value.startsWith('asset:'));
    expect(sw.length).toBe(2);
    expect(sw.every((o) => o.disabled)).toBe(true);
  });

  it('is hidden when there are no switches and no Decos', async () => {
    mock(base({ assets: [], decos: [], pins: {} }));
    const { container } = render(<ConnectedThrough id={13} wifi={false} />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
