import { describe, expect, it } from 'vitest';
import { edgeHandles } from './edgeSides';
import { NODE_H } from './mapLayout';

const at = (x: number, y: number) => ({ x, y });

describe('edgeHandles', () => {
  it('runs a link from the parent bottom to the child top when the child is below', () => {
    expect(edgeHandles(at(0, 0), at(40, 200))).toEqual({ sourceHandle: 'bottom', targetHandle: 'top' });
  });

  it('runs a link from the parent top to the child bottom when the child is above (no loop)', () => {
    expect(edgeHandles(at(0, 300), at(0, 100))).toEqual({ sourceHandle: 'top', targetHandle: 'bottom' });
  });

  it('uses the facing left and right edges for tiles side by side', () => {
    expect(edgeHandles(at(0, 0), at(300, 10))).toEqual({ sourceHandle: 'right', targetHandle: 'left' });
    expect(edgeHandles(at(300, 0), at(0, NODE_H - 1))).toEqual({ sourceHandle: 'left', targetHandle: 'right' });
  });

  it('goes vertical as soon as the tiles stop overlapping in height', () => {
    expect(edgeHandles(at(0, 0), at(300, NODE_H)).sourceHandle).toBe('bottom');
  });

  it('lets a pin fix its own end only, on either end or both', () => {
    expect(edgeHandles(at(0, 0), at(0, 200), 'right')).toEqual({ sourceHandle: 'right', targetHandle: 'top' });
    expect(edgeHandles(at(0, 0), at(0, 200), undefined, 'left')).toEqual({ sourceHandle: 'bottom', targetHandle: 'left' });
    expect(edgeHandles(at(0, 0), at(0, 200), 'top', 'bottom')).toEqual({ sourceHandle: 'top', targetHandle: 'bottom' });
  });

  it('handles tiles at the same spot', () => {
    expect(edgeHandles(at(5, 5), at(5, 5))).toEqual({ sourceHandle: 'right', targetHandle: 'left' });
  });
});
