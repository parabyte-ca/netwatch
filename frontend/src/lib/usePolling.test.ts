import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { usePolling } from './usePolling';

describe('usePolling', () => {
  it('ignores a slow answer for old deps', async () => {
    const resolvers: Record<string, (v: string) => void> = {};
    const fetcher = (key: string) => () => new Promise<string>((r) => { resolvers[key] = r; });
    const { result, rerender } = renderHook(({ k }) => usePolling(fetcher(k), 60_000, [k]), {
      initialProps: { k: '30d' },
    });
    rerender({ k: '24h' });
    await act(async () => resolvers['24h']!('fresh'));
    await act(async () => resolvers['30d']!('stale'));
    await waitFor(() => expect(result.current.data).toBe('fresh'));
  });
});
