import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Health } from './Health';

// uPlot needs a real browser (matchMedia, canvas); the speed chart is tested on its own.
vi.mock('uplot', () => ({ default: class { setSize() {} destroy() {} } }));

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
  groups: [
    { id: 3, name: 'Smart home', colour: 'emerald', icon: 'plug', quiet: true, devices: 9, online: 9, down_bytes: 2e9, up_bytes: 1e9,
      top_talker: 'Kasa plug', outages: 0, alerts: 0, quiet_events: 14, dns: { queries: 12000, blocked_pct: 41.5, failed_pct: 0 } },
    { id: 0, name: 'Ungrouped', colour: 'slate', icon: 'circle', quiet: false, devices: 4, online: 2, down_bytes: 0, up_bytes: 0,
      top_talker: null, outages: 1, alerts: 2, quiet_events: 0, dns: null },
  ],
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
    const table = screen.getByRole('table', { name: 'Groups this week' });
    expect(table).toHaveTextContent('Smart home (quiet)');
    expect(table).toHaveTextContent('12,000 · 41.5% blocked');
    expect(table).toHaveTextContent('n/a');
    expect(screen.getByRole('link', { name: 'Download (.md)' })).toHaveAttribute('href', '/api/weekly-reports/2026-10-05?format=md');
  });
});
