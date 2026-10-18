import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Health } from './Health';

afterEach(() => vi.restoreAllMocks());

const comps = [
  { key: 'internet', label: 'Internet uptime', weight: 30, score: 90, detail: '99.90 % available' },
  { key: 'load', label: 'Latency under load', weight: 15, score: 60, detail: 'bufferbloat grade C' },
];
const week = {
  week_start: '2026-10-05', created_ms: 1_760_000_000_000, score: 82, label: 'Good', previous_score: 76,
  components: comps, highlights: ['The internet dropped 1 time for 10 min in total (Bell network).'],
  period: { start: '2026-10-05 00:00:00 EDT', end: '2026-10-12 00:00:00 EDT', start_ms: 0, end_ms: 1 },
  summary: { incidents: 1, isp_incidents: 1, isp_downtime_ms: 600000, isp_downtime: '10 min', isp_availability_pct: 99.9, longest: '10 min' },
  speeds: [], incidents: [{ id: 3, start: '2026-10-06 02:00', duration: '10 min', cause: 'Bell network outage', severity: 'critical', start_ms: 1 }],
  incident_count: 1, markdown: '# x',
};

describe('Health', () => {
  it('shows the score, its breakdown and a weekly report', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
      if (url.startsWith('/api/score')) return json({ score: 84, label: 'Good', components: comps, highlights: ['No internet outages caused by the Bell network.'], start_ms: 0, end_ms: 1, days: 7 });
      if (url === '/api/weekly-reports') return json({ last_full_week: '2026-10-05', reports: [{ week_start: '2026-10-05', created_ms: 1, score: 82, label: 'Good', previous_score: 76, highlights: [], incident_count: 1 }] });
      if (url === '/api/weekly-reports/2026-10-05') return json(week);
      return json({});
    });
    render(<MemoryRouter><Health /></MemoryRouter>);
    expect(await screen.findByRole('img', { name: 'Health score 84 of 100' })).toBeInTheDocument();
    expect(screen.getByText('bufferbloat grade C')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /Week of 2026-10-05/ }));
    expect(await screen.findByText(/\+6 vs the week before/)).toBeInTheDocument();
    expect(screen.getByText(/Bell network outage/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download (.md)' })).toHaveAttribute('href', '/api/weekly-reports/2026-10-05?format=md');
  });
});
