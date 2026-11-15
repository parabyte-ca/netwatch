import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { IntegrationRow } from '../lib/api';
import { IntegrationHealthCard, IntegrationProblems } from './IntegrationHealth';

afterEach(() => vi.restoreAllMocks());
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const base = { error: null, last_ok_ms: null, reads_24h: null, fix: null };
const rows: IntegrationRow[] = [
  { ...base, key: 'deco', label: 'Deco mesh', state: 'ok', detail: 'Reading.', last_ok_ms: Date.now() - 30_000, reads_24h: { ok: 2870, total: 2880 } },
  { ...base, key: 'adguard', label: 'AdGuard Home', state: 'stale', detail: 'No good read for 40 min.', error: 'connection refused', fix: { action: 'admin', label: 'Open settings' } },
  { ...base, key: 'hub', label: 'Home Hub', state: 'not_polling', detail: 'Saved, but polling is off.', fix: { action: 'polling', label: 'Turn on polling' } },
  { ...base, key: 'bufferbloat', label: 'Nightly load and speed test', state: 'stale', detail: 'Last run failed.', fix: { action: 'bufferbloat', label: 'Run now' } },
];

describe('integration health', () => {
  it('lists problems first with the error, the read count and the fix', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ rows, problems: 2 }));
    render(<MemoryRouter><IntegrationHealthCard /></MemoryRouter>);
    const items = await screen.findAllByRole('listitem');
    expect(items[0]).toHaveTextContent('AdGuard Home');
    expect(items[0]).toHaveTextContent('connection refused');
    expect(screen.getByRole('link', { name: 'Open settings' })).toHaveAttribute('href', '/admin');
    expect(screen.getByText('2870 of 2880 reads ok in 24 h')).toBeInTheDocument();
  });

  it('turns polling on and runs a nightly job from the card', async () => {
    const posts: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (init?.method === 'POST') posts.push(String(input));
      return json(String(input).includes('/health') ? { rows, problems: 2 } : { started: true });
    });
    render(<MemoryRouter><IntegrationHealthCard /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Turn on polling' }));
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(posts).toEqual(['/api/admin/integrations/hub/polling', '/api/bufferbloat/run']));
  });

  it('shows the Overview line only when something has stopped', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ rows: rows.slice(0, 1), problems: 0 }));
    const { container, unmount } = render(<MemoryRouter><IntegrationProblems /></MemoryRouter>);
    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    f.mockResolvedValue(json({ rows, problems: 2 }));
    render(<MemoryRouter><IntegrationProblems /></MemoryRouter>);
    expect(await screen.findByRole('status')).toHaveTextContent('AdGuard Home and Nightly load and speed test have stopped working');
  });
});
