import type { MapEdge, MapNode, NetworkMapResponse, State } from './api';
import { colourOf } from './groups';

export type ColourBy = 'group' | 'status' | 'bandwidth';

export interface MapOptions {
  colourBy: ColourBy;
  groups: number[]; // filter: only these groups stay bright (empty: all)
  tags: string[]; // filter: only devices with one of these tags stay bright (empty: all)
  collapsed: string[]; // group ids shown as one node per parent
}

const ORDER: State[] = ['down', 'degraded', 'unknown', 'up'];
const worst = (states: State[]) => ORDER.find((s) => states.includes(s)) ?? 'unknown';

/** Live bits/s, or the average over the volume window. */
function avgBps(n: MapNode): number {
  if (n.traffic) return n.traffic.down_bps + n.traffic.up_bps;
  if (n.volume && n.volume.hours > 0) return ((n.volume.down_bytes + n.volume.up_bytes) * 8) / (n.volume.hours * 3600);
  return 0;
}

/** Bandwidth colour: quiet slate, then sky, amber and rose as the device gets busier. */
export function bandwidthHex(bps: number): string | null {
  if (bps < 1e5) return null;
  if (bps < 5e6) return '#38bdf8';
  if (bps < 50e6) return '#fbbf24';
  return '#fb7185';
}

/** Applies group collapse to the graph and works out each node's accent colour and whether it
 * is faded by the filters. Pure, so the map and its tests agree. */
export function shapeGraph(g: NetworkMapResponse, o: MapOptions): {
  nodes: MapNode[];
  edges: MapEdge[];
  accent: Map<string, string | null>;
  faded: Set<string>;
} {
  const collapsed = new Set(o.collapsed);
  const nodes: MapNode[] = [];
  const edges: MapEdge[] = [];
  const clusters = new Map<string, MapNode>();
  const hidden = new Set<string>();
  for (const n of g.nodes) {
    const gr = n.info.group;
    if (n.kind === 'device' && gr && collapsed.has(String(gr.id))) {
      const parent = n.info.parent ?? 'hub';
      const id = `grp:${gr.id}@${parent}`;
      let c = clusters.get(id);
      if (!c) {
        c = {
          id, kind: 'devgroup', label: gr.name, state: 'up', inferred: false, members: [],
          info: { group: gr, parent, count: 0, collapsed: [], devices: [] },
          traffic: { down_bps: 0, up_bps: 0 },
          volume: { down_bytes: 0, up_bytes: 0, hours: 0 },
        };
        clusters.set(id, c);
        nodes.push(c);
      }
      c.info.count = (c.info.count ?? 0) + 1;
      c.info.collapsed!.push(n.id);
      c.info.devices!.push({ device_id: n.info.device_id ?? 0, label: n.label, ip: n.info.ip ?? null,
        type: n.info.type ?? 'unknown', connection: n.info.connection ?? null });
      c.state = worst([c.state === 'up' && c.info.count === 1 ? n.state : c.state, n.state]);
      if (n.traffic) {
        c.traffic!.down_bps += n.traffic.down_bps;
        c.traffic!.up_bps += n.traffic.up_bps;
      }
      if (n.volume) {
        c.volume!.down_bytes += n.volume.down_bytes;
        c.volume!.up_bytes += n.volume.up_bytes;
        c.volume!.hours = n.volume.hours;
      }
      hidden.add(n.id);
      continue;
    }
    nodes.push(n);
  }
  for (const c of clusters.values()) {
    c.label = `${c.info.group!.name} · ${c.info.count}`;
    if (!c.traffic!.down_bps && !c.traffic!.up_bps) delete c.traffic;
    if (!c.volume!.down_bytes && !c.volume!.up_bytes) delete c.volume;
  }
  const clusterEdges = new Map<string, MapEdge>();
  for (const e of g.edges) {
    if (!hidden.has(e.target)) {
      edges.push(e);
      continue;
    }
    const n = g.nodes.find((x) => x.id === e.target)!;
    const id = `grp:${n.info.group!.id}@${e.source}`;
    if (!clusterEdges.has(id)) {
      const ce: MapEdge = { ...e, id: `${e.source}>${id}`, target: id, label: null, median_ms: null, loss_pct: null, traffic: undefined, volume: undefined };
      clusterEdges.set(id, ce);
      edges.push(ce);
    } else if (e.medium === 'wired') {
      clusterEdges.get(id)!.medium = 'wired';
    }
  }

  const accent = new Map<string, string | null>();
  const faded = new Set<string>();
  const filtering = o.groups.length > 0 || o.tags.length > 0;
  for (const n of nodes) {
    const gr = n.info.group;
    if (n.kind === 'device' || n.kind === 'devgroup') {
      if (o.colourBy === 'group') accent.set(n.id, gr ? colourOf(gr.colour).hex : null);
      else if (o.colourBy === 'bandwidth') accent.set(n.id, bandwidthHex(avgBps(n)));
      else accent.set(n.id, null);
      if (filtering) {
        const inGroup = o.groups.length === 0 || (!!gr && o.groups.includes(gr.id));
        const tagged = o.tags.length === 0 || (n.info.tags ?? []).some((t) => o.tags.includes(t));
        if (!(inGroup && tagged)) faded.add(n.id);
      }
    }
  }
  return { nodes, edges, accent, faded };
}
