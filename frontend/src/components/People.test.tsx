import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Person as P } from '../lib/api';
import { Person } from '../pages/Person';
import { HomeNow, PeopleManager, presenceText } from './People';

afterEach(() => vi.restoreAllMocks());

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const NOW = new Date('2026-11-15T18:00:00').getTime();
const sam: P = { id: 1, name: 'Sam', colour: 'sky', sort: 0, created_ms: 0, device_ids: [7], home: true, since_ms: NOW - 3_600_000, last_seen_ms: NOW, };
const kim: P = { ...sam, id: 2, name: 'Kim', colour: 'lime', home: false, since_ms: NOW - 7_200_000 };

describe('presenceText', () => {
  it('says home, away or no devices', () => {
    expect(presenceText(sam, NOW)).toBe('home since 17:00');
    expect(presenceText(kim, NOW)).toBe('away since 16:00');
    expect(presenceText({ ...sam, device_ids: [], home: null, since_ms: null }, NOW)).toBe('no devices yet');
  });
});

describe('HomeNow', () => {
  it('shows nothing until someone is added', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ people: [], settings: { away_after_min: 15 }, colours: [] }));
    const { container } = render(<MemoryRouter><HomeNow /></MemoryRouter>);
    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('lists people with home or away and links to their page', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ people: [sam, kim], settings: { away_after_min: 15 }, colours: [] }));
    render(<MemoryRouter><HomeNow /></MemoryRouter>);
    const strip = await screen.findByRole('region', { name: "Who's home" });
    expect(within(strip).getByRole('link', { name: /Sam home since/ })).toHaveAttribute('href', '/people/1');
    expect(within(strip).getByRole('link', { name: /Kim away since/ })).toBeInTheDocument();
  });
});

describe('PeopleManager', () => {
  it('adds a person with their phone and changes the away time', async () => {
    let people: P[] = [];
    const calls: { url: string; body: unknown }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (init?.method && init.method !== 'GET') calls.push({ url, body });
      if (url === '/api/people' && init?.method === 'POST') {
        people = [{ ...sam, ...body, id: 3 }];
        return json(people[0], 201);
      }
      if (url === '/api/people') return json({ people, settings: { away_after_min: 15 }, colours: [] });
      if (url === '/api/people-settings') return json(body);
      if (url.startsWith('/api/devices')) {
        return json({ now_ms: NOW, devices: [
          { id: 7, label: "Sam's iPhone", type: 'phone', online: true, merged_into: null, mac: 'a', last_ip: '192.168.2.40' },
          { id: 8, label: 'Kasa plug', type: 'iot', online: true, merged_into: null, mac: 'b', last_ip: '192.168.2.41' },
          { id: 9, label: 'Old MAC', type: 'phone', online: false, merged_into: 7, mac: 'c', last_ip: null },
        ] });
      }
      return json({}, 404);
    });
    render(<MemoryRouter><PeopleManager onClose={() => undefined} /></MemoryRouter>);
    expect(await screen.findByText(/Nobody yet/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('New person name'), { target: { value: 'Sam' } });
    const phone = await screen.findByRole('checkbox', { name: /Sam's iPhone/ });
    expect(screen.queryByText('Old MAC')).not.toBeInTheDocument(); // private-MAC siblings are folded in
    fireEvent.click(phone);
    fireEvent.click(screen.getByRole('button', { name: 'Add person' }));
    expect(await screen.findByRole('link', { name: 'Sam' })).toBeInTheDocument();
    expect(calls[0]).toEqual({ url: '/api/people', body: { name: 'Sam', colour: 'sky', device_ids: [7] } });
    fireEvent.change(screen.getByLabelText('Away after'), { target: { value: '30' } });
    await waitFor(() => expect(calls.at(-1)).toEqual({ url: '/api/people-settings', body: { away_after_min: 30 } }));
  });
});

describe('Person page', () => {
  it("shows today's arrivals and the last days", async () => {
    const start = new Date('2026-11-15T00:00:00').getTime();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({
      person: sam,
      devices: [{ id: 7, label: "Sam's iPhone" }],
      events: [{ ts_ms: NOW - 3_600_000, action: 'arrived', title: 'Sam arrived home at 17:00' }],
      days: [{ date: '2026-11-15', start_ms: start, spans: [
        { start_ms: start, end_ms: NOW - 3_600_000, home: false },
        { start_ms: NOW - 3_600_000, end_ms: NOW, home: true },
      ] }],
    }));
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true });
    render(<MemoryRouter initialEntries={['/people/1']}><Routes><Route path="/people/:id" element={<Person />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: 'Sam' })).toBeInTheDocument();
    expect(screen.getByText('Arrived home')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /home for 1 h/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: "Sam's iPhone" })).toHaveAttribute('href', '/devices/7');
    vi.useRealTimers();
  });
});
