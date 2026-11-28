import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DecoSchedule } from '../lib/api';
import { DecoDailyRestart } from './DecoDailyRestart';

afterEach(() => vi.restoreAllMocks());

const ALL = [0, 1, 2, 3, 4, 5, 6];
const sched = (over: Partial<DecoSchedule> = {}): DecoSchedule => ({
  enabled: false, time: '06:50', days: ALL, next_ms: null, last: null, actions: true, ...over,
});

type Call = { method: string; body?: Record<string, unknown> };
function mock(initial: DecoSchedule, calls: Call[]) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
    const method = init?.method ?? 'GET';
    if (method === 'PUT') {
      const body = JSON.parse(String(init?.body));
      calls.push({ method, body });
      return new Response(JSON.stringify({ ...initial, ...body, next_ms: body.enabled ? Date.now() + 3600_000 : null }));
    }
    return new Response(JSON.stringify(initial));
  });
}

describe('DecoDailyRestart', () => {
  it('turns the daily restart on and shows when it runs next', async () => {
    const calls: Call[] = [];
    mock(sched(), calls);
    render(<DecoDailyRestart />);
    fireEvent.click(await screen.findByLabelText('Restart the whole Wi-Fi network every day'));
    await waitFor(() => expect(calls).toContainEqual({ method: 'PUT', body: { enabled: true, time: '06:50', days: ALL } }));
    expect(await screen.findByText(/Next:/)).toBeInTheDocument();
  });

  it('saves a new time and weekdays', async () => {
    const calls: Call[] = [];
    mock(sched({ enabled: true, next_ms: Date.now() + 1000 }), calls);
    render(<DecoDailyRestart />);
    const time = await screen.findByLabelText('Restart time');
    fireEvent.change(time, { target: { value: '07:05' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sun' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls).toContainEqual({ method: 'PUT', body: { enabled: true, time: '07:05', days: [0, 1, 2, 3, 4, 5] } }));
  });

  it('shows what the last run did, and warns when actions are off but it is on', async () => {
    mock(sched({ enabled: true, actions: false, last: { ts_ms: Date.now() - 60_000, outcome: 'ran', summary: 'restart sent to Office, Living Room' } }), []);
    render(<DecoDailyRestart />);
    expect(await screen.findByText(/restart sent to Office, Living Room/)).toBeInTheDocument();
    expect(screen.getByText(/Deco actions are off/)).toBeInTheDocument();
  });

  it('stays hidden when Deco actions are off and nothing is scheduled', async () => {
    mock(sched({ actions: false }), []);
    const { container } = render(<DecoDailyRestart />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
