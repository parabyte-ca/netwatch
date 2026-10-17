import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PathTest } from '../lib/api';
import { HopTable } from './HopTable';

const base: PathTest = {
  id: 1, ts_ms: 1_760_000_000_000, target_id: 1, event_id: null, host: '1.1.1.1', trigger: 'outage', status: 'ok',
  duration_ms: 2000, hop_count: 4, reached: true, first_loss_hop: 3, path_changed: true, error: null,
  hops: [
    { hop: 1, host: '192.168.2.1', loss_pct: 0, sent: 10, last: 0.5, avg: 0.6, best: 0.4, worst: 0.9, stdev: 0.1 },
    { hop: 2, host: null, loss_pct: 100, sent: 10, last: null, avg: null, best: null, worst: null, stdev: null },
    { hop: 3, host: '10.11.12.13', loss_pct: 30, sent: 10, last: 5, avg: 6, best: 4, worst: 9, stdev: 1 },
    { hop: 4, host: '1.1.1.1', loss_pct: 20, sent: 10, last: 7, avg: 7.5, best: 6, worst: 12, stdev: 1.5 },
  ],
};

describe('HopTable', () => {
  it('highlights where loss starts and flags route changes', () => {
    render(<HopTable test={base} />);
    expect(screen.getByText('10.11.12.13').closest('tr')).toHaveClass('bg-rose-950/50');
    expect(screen.getByText('no reply')).toBeInTheDocument();
    expect(screen.getByText(/loss starts at hop 3/)).toBeInTheDocument();
    expect(screen.getByText(/route changed/)).toBeInTheDocument();
  });

  it('explains intermediate-only loss and shows errors', () => {
    const { rerender } = render(
      <HopTable test={{ ...base, first_loss_hop: null, path_changed: false, hops: base.hops.map((h) => (h.hop === 4 ? { ...h, loss_pct: 0 } : h)) }} />,
    );
    expect(screen.getByText(/ICMP rate-limiting/)).toBeInTheDocument();
    rerender(<HopTable test={{ ...base, status: 'error', error: 'mtr is not installed' }} />);
    expect(screen.getByText(/mtr is not installed/)).toBeInTheDocument();
  });
});
