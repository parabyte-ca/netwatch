import type { MapSide } from './api';
import { NODE_H } from './mapLayout';

export type Point = { x: number; y: number };

/** Which edge of each tile a link attaches to. Tiles side by side (their rows overlap) use the
 * facing left and right edges; otherwise the link leaves the edge that faces the other tile, so a
 * tile above its parent no longer makes the line loop around it. A tile pinned to a side keeps its
 * end of every link on that side; the other end stays automatic. Positions are tile top-lefts. */
export function edgeHandles(
  from: Point,
  to: Point,
  fromPin?: MapSide,
  toPin?: MapSide,
): { sourceHandle: MapSide; targetHandle: MapSide } {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  let source: MapSide;
  let target: MapSide;
  if (Math.abs(dy) < NODE_H) {
    source = dx >= 0 ? 'right' : 'left';
    target = dx >= 0 ? 'left' : 'right';
  } else {
    source = dy > 0 ? 'bottom' : 'top';
    target = dy > 0 ? 'top' : 'bottom';
  }
  return { sourceHandle: fromPin ?? source, targetHandle: toPin ?? target };
}
