import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { MapNode, NetworkMapResponse } from '../lib/api';
import { layoutMap } from '../lib/mapLayout';
import { NetworkMap } from './NetworkMap';

beforeAll(() => {
  // React Flow measures its container.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  (globalThis as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly ??= class {
    m22 = 1;
  };
});
afterEach(() => vi.restoreAllMocks());

const n = (id: string, kind: MapNode['kind'], label: string, over: Partial<MapNode> = {}): MapNode => ({
  id, kind, label, state: 'up', inferred: false, members: [], info: {}, ...over,
});

const graph: NetworkMapResponse = {
  generated_ms: Date.now(),
  at_ms: null,
  deco_api: true,
  nodes: [
    n('internet', 'internet', 'Internet'),
    n('isp', 'isp', 'Bell network', { inferred: true }),
    n('hub', 'hub', 'Bell Home Hub', { info: { ip: '192.168.2.1' } }),
    n('switch', 'switch', 'Switch', { inferred: true }),
    n('truenas', 'host', 'TrueNAS (NetWatch)'),
    n('deco:a', 'deco', 'Deco Living Room', { info: { role: 'main', backhaul: 'wired', ip: '192.168.2.10' } }),
    n('deco:b', 'deco', 'Deco Office', { state: 'down', info: { role: 'satellite', backhaul: '5 GHz' } }),
    n('group:deco:a', 'group', '2 more devices', {
      info: { count: 2, devices: [
        { device_id: 7, label: 'Pixel', ip: '192.168.2.50', type: 'phone', connection: '5 GHz' },
        { device_id: 8, label: 'Echo', ip: '192.168.2.51', type: 'speaker', connection: '2.4 GHz' },
      ] },
    }),
  ],
  edges: [
    { id: 'hub>isp', source: 'hub', target: 'isp', medium: 'logical', median_ms: null, loss_pct: null, label: null },
    { id: 'isp>internet', source: 'isp', target: 'internet', medium: 'logical', median_ms: 12, loss_pct: 0, label: 'internet' },
    { id: 'hub>switch', source: 'hub', target: 'switch', medium: 'wired', median_ms: null, loss_pct: null, label: null },
    { id: 'switch>truenas', source: 'switch', target: 'truenas', medium: 'wired', median_ms: 1, loss_pct: 0, label: null },
    { id: 'hub>deco:a', source: 'hub', target: 'deco:a', medium: 'wired', median_ms: 2, loss_pct: 0, label: null },
    { id: 'deco:a>deco:b', source: 'deco:a', target: 'deco:b', medium: 'wireless', median_ms: null, loss_pct: 30, label: '5 GHz' },
    { id: 'deco:a>group:deco:a', source: 'deco:a', target: 'group:deco:a', medium: 'logical', median_ms: null, loss_pct: null, label: null },
  ],
};

function mockApi(calls: { url: string; method: string; body?: unknown }[], g = graph, positions = {}) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body });
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url === '/api/map/layout' && method === 'DELETE') return new Response(null, { status: 204 });
    if (url === '/api/map/layout') return json({ positions });
    if (url.startsWith('/api/map')) return json(g);
    if (url.startsWith('/api/diagnosis')) return json({ now_ms: Date.now(), topology: [], patterns: [], incidents: [] });
    return json({});
  });
}

describe('layoutMap', () => {
  it('puts the internet on top, devices below, and keeps saved positions', () => {
    const pos = layoutMap(graph.nodes, graph.edges);
    expect(pos.internet!.y).toBeLessThan(pos.hub!.y);
    expect(pos.hub!.y).toBeLessThan(pos['deco:a']!.y);
    expect(pos['deco:a']!.y).toBeLessThan(pos['deco:b']!.y);
    expect(layoutMap(graph.nodes, graph.edges, { hub: { x: 5, y: 6 } }).hub).toEqual({ x: 5, y: 6 });
  });
});

describe('NetworkMap', () => {
  it('draws the network and opens a group to list its devices', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    mockApi(calls);
    render(<MemoryRouter initialEntries={['/map']}><NetworkMap /></MemoryRouter>);
    expect(await screen.findByText('Deco Office')).toBeInTheDocument();
    expect(screen.getByText('Bell Home Hub')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('node-group:deco:a'));
    const panel = await screen.findByRole('complementary', { name: 'Node details' });
    expect(panel).toHaveTextContent('Pixel');
    expect(panel).toHaveTextContent('Echo');
    expect(calls.some((c) => c.url === '/api/map')).toBe(true);
  });

  it('replays an incident and marks the likely cause', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    const start = Date.now() - 3600_000;
    mockApi(calls, {
      ...graph,
      at_ms: start + 30_000,
      highlight: {
        incident_id: `i${start}`, start_ms: start, failing_nodes: ['deco:b'], cause_nodes: ['deco:b'],
        cause: { id: 'deco_offline', title: 'Deco Office went offline', layer: 'mesh', confidence: 92, evidence: [], next_steps: [] },
      },
    });
    render(<MemoryRouter initialEntries={[`/map?incident=i${start}`]}><NetworkMap /></MemoryRouter>);
    expect(await screen.findByText('likely cause')).toBeInTheDocument();
    expect(screen.getByText('Deco Office went offline')).toBeInTheDocument();
    expect(screen.getByLabelText('Replay slider')).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url === `/api/map?incident=i${start}`)).toBe(true));
  });

  it('resets a saved layout', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    mockApi(calls, graph, { hub: { x: 1, y: 2 } });
    render(<MemoryRouter><NetworkMap /></MemoryRouter>);
    const reset = await screen.findByRole('button', { name: 'Reset layout' });
    await waitFor(() => expect(reset).toBeEnabled());
    fireEvent.click(reset);
    await waitFor(() => expect(calls.some((c) => c.url === '/api/map/layout' && c.method === 'DELETE')).toBe(true));
  });
});
