import { describe, expect, it } from 'vitest';
import type { MapEdge, MapNode } from './api';
import { layoutMap } from './mapLayout';

const node = (id: string, kind: MapNode['kind'], label = id): MapNode => ({ id, kind, label, state: 'up', inferred: false, members: [], info: {} });
const edge = (source: string, target: string): MapEdge => ({ id: `${source}>${target}`, source, target, medium: 'logical', median_ms: null, loss_pct: null, label: null });

describe('layoutMap with remote servers', () => {
  const nodes = [node('internet', 'internet'), node('isp', 'isp'), node('hub', 'hub'), node('remote:1', 'remote', 'Cloud VPS'), node('remote:2', 'remote', 'Office')];
  const edges = [edge('hub', 'isp'), edge('isp', 'internet'), edge('internet', 'remote:1'), edge('internet', 'remote:2')];

  it('puts every remote server above the Internet node, which stays above Bell and the Hub', () => {
    const p = layoutMap(nodes, edges);
    expect(p['remote:1']!.y).toBeLessThan(p.internet!.y);
    expect(p['remote:2']!.y).toBeLessThan(p.internet!.y);
    expect(p.internet!.y).toBeLessThan(p.isp!.y);
    expect(p.isp!.y).toBeLessThan(p.hub!.y);
  });

  it('keeps the two remote servers side by side, not on top of each other', () => {
    const p = layoutMap(nodes, edges);
    expect(p['remote:1']!.y).toBe(p['remote:2']!.y);
    expect(Math.abs(p['remote:1']!.x - p['remote:2']!.x)).toBeGreaterThan(100);
  });

  it('leaves the map as before when there are none, and honours a dragged position', () => {
    const plain = layoutMap(nodes.slice(0, 3), edges.slice(0, 2));
    expect(Object.keys(plain)).toEqual(['internet', 'isp', 'hub']);
    const dragged = layoutMap(nodes, edges, { 'remote:1': { x: 5, y: 7 } });
    expect(dragged['remote:1']).toEqual({ x: 5, y: 7 });
  });
});
