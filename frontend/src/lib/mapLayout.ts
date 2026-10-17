import { Graph, layout } from '@dagrejs/dagre';
import type { MapEdge, MapNode, MapPositions } from './api';

export const NODE_W = 184;
export const NODE_H = 64;

/** Top-to-bottom automatic layout (Internet at the top, devices at the bottom). Saved
 * positions win over the computed ones, so a node you dragged stays where you put it. */
export function layoutMap(nodes: MapNode[], edges: MapEdge[], saved: MapPositions = {}): MapPositions {
  const g = new Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 28, ranksep: 70, marginx: 10, marginy: 10 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  // The Internet sits above the Hub, so lay that part out in reverse (parent above child).
  for (const e of edges) {
    const up = e.target === 'isp' || e.target === 'internet';
    if (up) g.setEdge(e.target, e.source);
    else g.setEdge(e.source, e.target);
  }
  layout(g);
  const out: MapPositions = {};
  for (const n of nodes) {
    const p = g.node(n.id);
    out[n.id] = saved[n.id] ?? { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - NODE_H / 2) };
  }
  return out;
}
