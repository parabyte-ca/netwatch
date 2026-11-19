import { Graph, layout } from '@dagrejs/dagre';
import type { MapEdge, MapNode, MapPositions } from './api';

export const NODE_W = 184;
export const NODE_H = 64;
const WRAP_AFTER = 8; // a parent with more leaf devices than this gets them in a grid
const COLS = 6;
const GAP_X = 16;
const GAP_Y = 22;

const isLeafKind = (k?: string) => k === 'device' || k === 'devgroup' || k === 'group';

/** Top-to-bottom automatic layout (Internet at the top, devices at the bottom). Saved
 * positions win over the computed ones, so a node you dragged stays where you put it.
 *
 * With every device on the map a Deco can have dozens of devices. Those are laid out as one
 * grid block (COLS wide, grouped devices together), which dagre places like a single node, so
 * the rest of the map stays compact. */
export function layoutMap(nodes: MapNode[], edges: MapEdge[], saved: MapPositions = {}): MapPositions {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const hasChildren = new Set(edges.filter((e) => e.target !== 'isp' && e.target !== 'internet').map((e) => e.source));
  const leaves = new Map<string, string[]>();
  for (const e of edges) {
    if (isLeafKind(byId.get(e.target)?.kind) && !hasChildren.has(e.target)) {
      leaves.set(e.source, [...(leaves.get(e.source) ?? []), e.target]);
    }
  }
  const gridded = new Map<string, string[]>(); // parent → its leaves, in display order
  for (const [parent, list] of leaves) {
    if (list.length > WRAP_AFTER) {
      // Grouped devices first, group by group, then the ungrouped ones; by name inside each.
      const cmp = (a: string, b: string) => {
        const x = byId.get(a)!, y = byId.get(b)!;
        const gx = x.info.group?.name, gy = y.info.group?.name;
        if (!!gx !== !!gy) return gx ? -1 : 1;
        return (gx ?? '').localeCompare(gy ?? '') || x.label.localeCompare(y.label, undefined, { numeric: true });
      };
      gridded.set(parent, [...list].sort(cmp));
    }
  }
  const inGrid = new Set([...gridded.values()].flat());

  const g = new Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 28, ranksep: 70, marginx: 10, marginy: 10 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) if (!inGrid.has(n.id)) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  const size = (count: number) => {
    const cols = Math.min(COLS, count);
    const rows = Math.ceil(count / COLS);
    return { width: cols * NODE_W + (cols - 1) * GAP_X, height: rows * NODE_H + (rows - 1) * GAP_Y };
  };
  for (const [parent, list] of gridded) g.setNode(`grid:${parent}`, size(list.length));
  // The Internet sits above the Hub, so lay that part out in reverse (parent above child).
  for (const e of edges) {
    if (inGrid.has(e.target)) continue;
    // A remote server sits above the Internet node, outside the network.
    const up = e.target === 'isp' || e.target === 'internet' || byId.get(e.target)?.kind === 'remote';
    if (up) g.setEdge(e.target, e.source);
    else g.setEdge(e.source, e.target);
  }
  for (const parent of gridded.keys()) g.setEdge(parent, `grid:${parent}`);
  layout(g);

  const out: MapPositions = {};
  for (const n of nodes) {
    if (inGrid.has(n.id)) continue;
    const p = g.node(n.id);
    out[n.id] = { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - NODE_H / 2) };
  }
  for (const [parent, list] of gridded) {
    const box = g.node(`grid:${parent}`);
    const left = box.x - box.width / 2;
    const top = box.y - box.height / 2;
    list.forEach((id, i) => {
      out[id] = { x: Math.round(left + (i % COLS) * (NODE_W + GAP_X)), y: Math.round(top + Math.floor(i / COLS) * (NODE_H + GAP_Y)) };
    });
  }
  for (const id of Object.keys(out)) if (saved[id]) out[id] = saved[id]!;
  return out;
}
