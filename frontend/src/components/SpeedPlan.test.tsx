import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { withPlan } from '../pages/Diagnostics';
import { ofPlan, SpeedPlanCard } from './SpeedPlan';

// uPlot needs a real browser (matchMedia, canvas); the chart itself is not under test here.
vi.mock('uplot', () => ({ default: class { setSize() {} destroy() {} } }));

afterEach(() => vi.restoreAllMocks());
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const plan = { plan_down_mbps: 1500, plan_up_mbps: 940, plan_slow_pct: 70 };
const pt = (ts: number, d: number, u: number, extra = {}) => ({
  ts_ms: ts, trigger: 'scheduled', down_mbps: d, up_mbps: u, down_pct: (d / 1500) * 100, up_pct: (u / 940) * 100,
  plan_down_mbps: 1500, plan_up_mbps: 940, link_mbps: null, link_capped: false, ...extra,
});

describe('speed against plan', () => {
  it('formats a result with its share of the plan', () => {
    expect(ofPlan(1380, 92)).toBe('1,380 Mb/s (92 % of plan)');
    expect(withPlan(940e6, 1500)).toMatch(/\(63 % of plan\)$/);
    expect(withPlan(940e6, undefined)).not.toMatch(/plan/);
  });

  it('asks for the plan speeds when they are not set', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ plan: { ...plan, plan_down_mbps: 0, plan_up_mbps: 0 }, set: false, points: [], last: null,
      median_30d: { down_mbps: null, up_mbps: null, down_pct: null, up_pct: null, tests: 0 }, open_event_id: null }));
    render(<MemoryRouter><SpeedPlanCard /></MemoryRouter>);
    expect(await screen.findByRole('link', { name: 'Admin → Internet plan' })).toHaveAttribute('href', '/admin');
  });

  it('shows the last result, medians, a slow-nights notice and the port limit', async () => {
    const now = Date.now();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({
      plan, set: true,
      points: [pt(now - 2 * 86_400_000, 1400, 900), pt(now - 86_400_000, 940, 600, { link_mbps: 1000, link_capped: true })],
      last: pt(now - 86_400_000, 940, 600, { link_mbps: 1000, link_capped: true }),
      median_30d: { down_mbps: 1170, up_mbps: 750, down_pct: 78, up_pct: 79.8, tests: 2 }, open_event_id: 12,
    }));
    render(<MemoryRouter><SpeedPlanCard /></MemoryRouter>);
    expect(await screen.findByText('63 %')).toBeInTheDocument(); // last download, 940 of 1500
    expect(screen.getByText('78 %')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Details' })).toHaveAttribute('href', '/events/12');
    expect(screen.getByText(/port runs at 1,000 Mb\/s and was the limit/)).toBeInTheDocument();
    expect(screen.getByTestId('speed-plan-chart')).toBeInTheDocument();
  });
});
