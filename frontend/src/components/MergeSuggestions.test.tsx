import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { MergeSuggestion } from '../lib/api';
import { MergeSuggestions } from './MergeSuggestions';

afterEach(() => vi.restoreAllMocks());

const side = (id: number, label: string, online: boolean, monitored = false) => ({
  id, label, online, last_seen_ms: online ? Date.now() : Date.now() - 3 * 3600_000, last_ip: `192.168.2.${id}`, monitored,
});
const pair = (over: Partial<MergeSuggestion> = {}): MergeSuggestion => ({
  new: side(1, 'truenas-server', true), old: side(2, 'TrueNAS Server', false), reasons: ['same hostname (truenas-server)'], both_monitored: false, ...over,
});

function mock(items: unknown) {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === '/api/devices/merge-suggestions') return new Response(JSON.stringify(items));
    return new Response(JSON.stringify({ device: { id: 2 } }));
  });
  return calls;
}

describe('MergeSuggestions', () => {
  it('shows nothing when there are no suggestions or the reply is unexpected', async () => {
    mock([]);
    const { container, unmount } = render(<MergeSuggestions onDone={async () => undefined} />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    unmount();
    vi.restoreAllMocks();
    mock({});
    const again = render(<MergeSuggestions onDone={async () => undefined} />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(again.container).toBeEmptyDOMElement();
  });

  it('says which device stays and merges the new card into the older entry', async () => {
    const calls = mock([pair()]);
    const done = vi.fn(async () => undefined);
    render(<MergeSuggestions onDone={done} />);
    const card = await screen.findByRole('region', { name: 'Same machine?' });
    expect(card).toHaveTextContent('truenas-server (online now, 192.168.2.1) looks like TrueNAS Server (last seen 3h ago, 192.168.2.2)');
    expect(card).toHaveTextContent('Why: same hostname (truenas-server).');
    expect(card).toHaveTextContent('TrueNAS Server stays.');
    fireEvent.click(screen.getByRole('button', { name: 'Merge truenas-server into TrueNAS Server' }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(calls[0]).toMatchObject({ url: '/api/devices/1/merge', method: 'POST', body: { into: 2 } });
  });

  it('cannot merge two monitored devices, and "Not the same" dismisses the pair', async () => {
    const calls = mock([pair({ both_monitored: true, new: side(1, 'moogbox', true, true), old: side(2, 'moogbox old', false, true) })]);
    render(<MergeSuggestions onDone={async () => undefined} />);
    const card = await screen.findByRole('region', { name: 'Same machine?' });
    expect(card).toHaveTextContent('Both are monitored. Stop monitoring one of them on its page first');
    expect(screen.queryByRole('button', { name: /^Merge / })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'moogbox and moogbox old are not the same' }));
    await waitFor(() => expect(calls[0]).toMatchObject({ url: '/api/devices/merge-suggestions/dismiss', body: { a: 1, b: 2 } }));
  });

  it('shows the reason when a merge is refused', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) =>
      String(input) === '/api/devices/merge-suggestions' && !init?.method
        ? new Response(JSON.stringify([pair()]))
        : new Response(JSON.stringify({ detail: 'already the same device' }), { status: 409 }));
    render(<MergeSuggestions onDone={async () => undefined} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Merge truenas-server into TrueNAS Server' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already the same device');
  });
});
