import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, OfflineError } from '../lib/api';
import { OfflineBanner } from './OfflineBanner';

afterEach(() => vi.restoreAllMocks());

describe('OfflineBanner', () => {
  it('appears when NetWatch does not answer and goes once it does', async () => {
    render(<OfflineBanner />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await act(async () => {
      await expect(api.targets()).rejects.toBeInstanceOf(OfflineError);
    });
    expect(screen.getByRole('status')).toHaveTextContent('No connection to NetWatch');
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response('[]', { status: 200 }));
    await act(async () => {
      await api.targets();
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('stays out of the way while an update restarts NetWatch', async () => {
    render(<OfflineBanner hidden />);
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await act(async () => {
      await api.targets().catch(() => undefined);
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    // Leave the shared state online for other tests.
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response('[]', { status: 200 }));
    await api.targets();
  });

  it('does not treat an HTTP error as being offline', async () => {
    render(<OfflineBanner />);
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response('{}', { status: 500 }));
    await act(async () => {
      await api.targets().catch(() => undefined);
    });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
