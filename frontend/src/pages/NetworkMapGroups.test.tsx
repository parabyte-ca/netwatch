import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { MapNode, NetworkMapResponse } from '../lib/api';
import { shapeGraph } from '../lib/mapGroups';
import { layoutMap } from '../lib/mapLayout';
import { NetworkMap } from './NetworkMap';

beforeAll(() => {
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

const home = { id: 1, name: 'Smart home', colour: 'emerald', icon: 'plug' };
const kids = { id: 2, name: 'Kids', colour: 'rose', icon: 'child' };
const dev = (id: number, label: string, over: Partial<MapNode['info']> = {}, traffic?: MapNode['traffic']): MapNode => ({
  id: `dev:${id}`, kind: 'device', label, state: 'up', inferred: false, members: [], traffic,
  info: { device_id: id, parent: 'deco:a', tags: [], group: null, ...over },
});
const edge = (target: string, medium: 'wired' | 'wireless' = 'wireless') => ({
  id: `deco:a>${target}`, source: 'deco:a', target, medium, median_ms: null, loss_pct: null, label: null,
});

const graph: NetworkMapResponse = {
  generated_ms: Date.now(), at_ms: null, deco_api: true,
  groups: [{ ...home, nodes: ['dev:1', 'dev:2'] }, { ...kids, nodes: ['dev:3'] }],
  nodes: [
    { id: 'hub', kind: 'hub', label: 'Hub', state: 'up', inferred: false, members: [], info: {} },
    { id: 'deco:a', kind: 'deco', label: 'Deco Living Room', state: 'up', inferred: false, members: [], info: {} },
    dev(1, 'Kasa plug', { group: home, tags: ['garage'] }, { down_bps: 2e5, up_bps: 1e4 }),
    dev(2, 'Doorbell', { group: home }),
    dev(3, 'Sam iPad', { group: kids, tags: ['upstairs', 'school', 'ipad'] }),
    dev(4, 'Printer'),
  ],
  edges: [
    { id: 'hub>deco:a', source: 'hub', target: 'deco:a', medium: 'wired', median_ms: null, loss_pct: null, label: null },
    edge('dev:1'), edge('dev:2', 'wired'), edge('dev:3'), edge('dev:4'),
  ],
};

describe('shapeGraph', () => {
  it('colours by group, fades what the filters leave out, and folds collapsed groups', () => {
    const s = shapeGraph(graph, { colourBy: 'group', groups: [], tags: [], collapsed: [] });
    expect(s.accent.get('dev:1')).toBe('#34d399');
    expect(s.accent.get('dev:4')).toBeNull();
    expect(s.faded.size).toBe(0);
    const f = shapeGraph(graph, { colourBy: 'status', groups: [2], tags: [], collapsed: [] });
    expect([...f.faded].sort()).toEqual(['dev:1', 'dev:2', 'dev:4']);
    expect(f.accent.get('dev:1')).toBeNull();
    const t = shapeGraph(graph, { colourBy: 'bandwidth', groups: [], tags: ['garage'], collapsed: [] });
    expect([...t.faded].sort()).toEqual(['dev:2', 'dev:3', 'dev:4']);
    expect(t.accent.get('dev:1')).toBe('#38bdf8');
    const c = shapeGraph(graph, { colourBy: 'group', groups: [], tags: [], collapsed: ['1'] });
    const cluster = c.nodes.find((n) => n.id === 'grp:1@deco:a')!;
    expect(cluster.label).toBe('Smart home · 2');
    expect(cluster.info.devices!.map((d) => d.label)).toEqual(['Kasa plug', 'Doorbell']);
    expect(cluster.traffic!.down_bps).toBe(2e5);
    expect(c.nodes.some((n) => n.id === 'dev:1')).toBe(false);
    const ce = c.edges.filter((e) => e.target === 'grp:1@deco:a');
    expect(ce).toHaveLength(1);
    expect(ce[0]!.medium).toBe('wired'); // one of its devices is wired
  });

  it('wraps many devices under one parent into rows', () => {
    const many: NetworkMapResponse = {
      ...graph,
      nodes: [graph.nodes[0]!, graph.nodes[1]!, ...Array.from({ length: 14 }, (_, i) => dev(10 + i, `Device ${i}`))],
      edges: [graph.edges[0]!, ...Array.from({ length: 14 }, (_, i) => edge(`dev:${10 + i}`))],
    };
    const pos = layoutMap(many.nodes, many.edges);
    const rows = new Set(Array.from({ length: 14 }, (_, i) => pos[`dev:${10 + i}`]!.y));
    expect(rows.size).toBe(3); // 6 + 6 + 2
    // Grouped devices come first in the grid, then the ungrouped ones.
    const grouped: NetworkMapResponse = {
      ...many,
      nodes: many.nodes.map((n, i) => (i === many.nodes.length - 1 ? { ...n, info: { ...n.info, group: kids } } : n)),
    };
    const p2 = layoutMap(grouped.nodes, grouped.edges);
    const first = Object.entries(p2).filter(([id]) => id.startsWith('dev:')).sort((a, b) => a[1].y - b[1].y || a[1].x - b[1].x)[0]![0];
    expect(first).toBe('dev:23');
  });
});

describe('NetworkMap: groups', () => {
  it('shows the group legend, collapses a group and saves it, and saves a view', async () => {
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
      if (url === '/api/map/layout') return json({ positions: {}, collapsed: [] });
      if (url === '/api/map/views') return json({ views: method === 'PUT' ? JSON.parse(String(init!.body)).views : [] });
      if (url.startsWith('/api/map')) return json(graph);
      return json({});
    });
    render(<MemoryRouter><NetworkMap /></MemoryRouter>);
    const legend = await screen.findByRole('list', { name: 'Groups on the map' });
    expect(within(legend).getByText('Smart home')).toBeInTheDocument();
    expect(await screen.findByText('Kasa plug')).toBeInTheDocument();
    const canvas = screen.getByTestId('map-canvas');
    expect(within(canvas).getByText('#garage')).toBeInTheDocument();
    expect(within(canvas).getByText('+1')).toBeInTheDocument(); // Sam iPad has 3 tags, 2 shown
    fireEvent.click(within(legend).getByRole('button', { name: 'Collapse Smart home' }));
    expect(await screen.findByText('Smart home · 2')).toBeInTheDocument();
    expect(screen.queryByText('Kasa plug')).not.toBeInTheDocument();
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/map/layout', method: 'PUT', body: { positions: {}, collapsed: ['1'] } }));
    fireEvent.change(screen.getByLabelText('View name'), { target: { value: 'Smart home folded' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/api/map/views' && c.method === 'PUT'
      && JSON.stringify(c.body).includes('"collapsed":["1"]'))).toBe(true));
    fireEvent.click(screen.getByLabelText('Show offline (24 h)'));
    await waitFor(() => expect((globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .some(([u]) => String(u).includes('show_offline=true'))).toBe(true));
  });
});
