import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SchedulesState } from '../lib/api';
import { ActiveNow, ScheduleList, TargetSchedules, daysText } from './Schedules';

afterEach(() => vi.restoreAllMocks());

const now = Date.now();
const kids = { kind: 'group' as const, id: 3 };
const state = (over: Partial<SchedulesState> = {}): SchedulesState => ({
  schedules: [{
    id: 'abc', name: 'Bedtime', target: kids, days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], start: '21:00', end: '07:00',
    effects: { internet_off: true, youtube_off: false, strict: false, services: [] }, enabled: true,
    next: { start_ms: now - 3_600_000, end_ms: now + 3_600_000 }, off_until_ms: null, summary: 'internet off', target_label: 'Kids',
  }],
  active: [{ id: 'abc', name: 'Bedtime', target: kids, effects: { internet_off: true, youtube_off: false, strict: false, services: [] },
    start_ms: now - 3_600_000, end_ms: now + 3_600_000, manual: false, target_label: 'Kids' }],
  presets: [{ name: 'School nights', days: ['sun', 'mon', 'tue', 'wed', 'thu'], start: '21:00', end: '07:00', effects: { internet_off: true } }],
  tz: 'America/Toronto',
  ...over,
});

function mock(s: SchedulesState | 'off') {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method !== 'GET') calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });
    if (url === '/api/adguard/actions/schedules' && method === 'GET') return s === 'off' ? json({ detail: 'off' }, 403) : json(s);
    return json({ state: s === 'off' ? null : s, off_until_ms: now + 900_000 });
  });
  return calls;
}

const services = [{ id: 'tiktok', name: 'TikTok' }];

describe('Schedules', () => {
  it('describes days the way people say them', () => {
    expect(daysText(['sun', 'mon', 'tue', 'wed', 'thu'])).toBe('Sun to Thu');
    expect(daysText(['mon', 'tue', 'wed', 'thu', 'fri'])).toBe('Weekdays');
    expect(daysText(['sat', 'sun'])).toBe('Weekends');
    expect(daysText(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'])).toBe('Every day');
    expect(daysText(['mon', 'wed'])).toBe('Mon, Wed');
  });

  it('shows nothing while AdGuard Home actions are off', async () => {
    mock('off');
    const { container } = render(<><TargetSchedules target={kids} label="Kids" services={services} /><ActiveNow /><ScheduleList /></>);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('lists a group schedule that is on now and gives 15 more minutes', async () => {
    const calls = mock(state());
    render(<TargetSchedules target={kids} label="Kids" services={services} />);
    const box = await screen.findByRole('region', { name: 'Schedules for Kids' });
    expect(box).toHaveTextContent('Bedtime');
    expect(box).toHaveTextContent('Every day 21:00–07:00 · internet off');
    expect(box).toHaveTextContent('On now until');
    fireEvent.click(within(box).getByRole('button', { name: '+15 min' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/schedules/abc/extend', method: 'POST', body: { how: '15' } }));
  });

  it('adds a schedule from a preset after confirming', async () => {
    const calls = mock(state({ schedules: [], active: [] }));
    render(<TargetSchedules target={kids} label="Kids" services={services} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add a schedule' }));
    const ed = screen.getByRole('group', { name: 'Schedule editor' });
    fireEvent.click(within(ed).getByRole('button', { name: 'School nights' }));
    expect(within(ed).getByLabelText('Schedule name')).toHaveValue('School nights');
    expect(within(ed).getByText('ends the next morning')).toBeInTheDocument();
    fireEvent.click(within(ed).getByRole('button', { name: 'Save schedule' }));
    const dlg = screen.getByRole('dialog', { name: 'Add the schedule School nights for Kids?' });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/schedules', method: 'POST', body: {
      name: 'School nights', target: kids, days: ['sun', 'mon', 'tue', 'wed', 'thu'], start: '21:00', end: '07:00',
      effects: { internet_off: true, youtube_off: false, strict: false, services: [] }, enabled: true } }));
  });

  it('turns the internet off now for a device after confirming', async () => {
    const calls = mock(state({ schedules: [], active: [] }));
    render(<TargetSchedules target={{ kind: 'device', id: 9 }} label="Console" services={services} />);
    fireEvent.click(await screen.findByRole('button', { name: '1 h' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Turn the internet off for Console for 1 hour?' })).getByRole('button', { name: 'Turn off' }));
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/adguard/actions/schedules/now', method: 'POST', body: { target: { kind: 'device', id: 9 }, minutes: 60 } }));
  });

  it('shows what is active now on the Overview, with +15 min', async () => {
    const calls = mock(state());
    render(<ActiveNow />);
    const strip = await screen.findByRole('region', { name: 'Schedules active now' });
    expect(strip).toHaveTextContent('Kids');
    expect(strip).toHaveTextContent('internet off (Bedtime) until');
    fireEvent.click(within(strip).getByRole('button', { name: '+15 min' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/abc/extend'))).toBe(true));
  });
});
