import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

describe('api client', () => {
  it('returns parsed JSON', async () => {
    const f = mockFetch(200, [{ id: 1 }]);
    await expect(api.targets()).resolves.toEqual([{ id: 1 }]);
    expect(f).toHaveBeenCalledWith('/api/targets', expect.objectContaining({ headers: expect.any(Object) }));
  });

  it('handles 204', async () => {
    mockFetch(204, null);
    await expect(api.deleteTarget(3)).resolves.toBeUndefined();
  });

  it('turns FastAPI validation errors into readable messages', async () => {
    mockFetch(422, { detail: [{ loc: ['body', 'host'], msg: 'Value error, must be an IP address or a valid hostname' }] });
    const err = await api.createTarget({ name: 'x', kind: 'icmp', interval_s: 1, enabled: true, group: 'custom' }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe('host: must be an IP address or a valid hostname');
  });

  it('builds export URLs', () => {
    expect(api.exportUrl(2, 10, 20, 'csv')).toBe('/api/targets/2/export?start=10&end=20&format=csv');
  });
});
