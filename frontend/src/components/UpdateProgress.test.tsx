import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UpdateProgressInfo, UpdateStage } from '../lib/api';
import { progressFraction, writePending } from '../lib/updateProgress';
import { UpdateProgress } from './UpdateProgress';

const T0 = Date.parse('2026-11-14T15:00:00Z');
const KEYS = ['requested', 'started', 'backup', 'download', 'restart', 'health', 'done'];
const LABELS = ['Requested', 'Agent started', 'Backup', 'Download and verify', 'Restart', 'Health check', 'Done'];
const stages = (upTo: string): UpdateStage[] =>
  KEYS.map((key, i) => ({ key, label: LABELS[i]!, at: T0 / 1000 + i * 10, reached: i <= KEYS.indexOf(upTo) }));

const running = (upTo: string, extra: Partial<UpdateProgressInfo> = {}): UpdateProgressInfo => ({
  state: 'running', action: 'update', stages: stages(upTo), stage: upTo, estimate_s: 200,
  status: { id: 'x', action: 'update', version: '0.12.0', state: 'running', started_at: '2026-11-14T15:00:00Z' },
  last_line: 'Pulling ghcr.io/parabyte-ca/netwatch:0.12.0 ...', ...extra,
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  vi.setSystemTime(T0 + 60_000);
  writePending(null);
});
afterEach(() => {
  vi.useRealTimers();
  writePending(null);
});

const bar = () => Number(screen.getByRole('progressbar').getAttribute('aria-valuenow'));

describe('progressFraction', () => {
  it('stays inside the current stage slice and reaches 1 only when done', () => {
    const s = stages('download'); // 4th of 7: slice 0.5 to 0.667
    expect(progressFraction(s, 0, 200, false)).toBeCloseTo(0.5);
    expect(progressFraction(s, 150, 200, false)).toBeLessThan(4 / 6);
    expect(progressFraction(s, 9999, 200, false)).toBeLessThan(4 / 6);
    expect(progressFraction(s, 0, 200, true)).toBe(1);
  });
});

describe('UpdateProgress', () => {
  it('shows the steps, timer, estimate and latest log line, and the bar never goes back', () => {
    const { rerender } = render(<UpdateProgress progress={running('download')} current="0.11.0" offline={false} />);
    expect(screen.getByText('Updating to v0.12.0')).toBeInTheDocument();
    expect(screen.getByLabelText('Time elapsed')).toHaveTextContent('1:00');
    expect(screen.getByRole('list', { name: 'Update steps' }).children).toHaveLength(7);
    expect(screen.getAllByText('Download and verify')[0]!.closest('li')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText(/About 3 min left · usually takes 3:20/)).toBeInTheDocument();
    expect(screen.getByLabelText('Latest log line')).toHaveTextContent('Pulling ghcr.io');
    expect(screen.getByText('Backup').closest('li')).toHaveTextContent('0:10'); // finished steps show their time
    const first = bar();
    expect(first).toBeGreaterThanOrEqual(50);
    // A stale poll that reports an earlier stage must not pull the bar back.
    rerender(<UpdateProgress progress={running('backup')} current="0.11.0" offline={false} />);
    expect(bar()).toBeGreaterThanOrEqual(first);
    act(() => vi.advanceTimersByTime(30_000));
    expect(screen.getByLabelText('Time elapsed')).toHaveTextContent('1:30');
  });

  it('treats an unanswered server after the agent started as restarting', () => {
    render(<UpdateProgress progress={running('download')} current="0.11.0" offline />);
    expect(screen.getByText(/restarting with the new version/)).toBeInTheDocument();
    expect(screen.getAllByText('Restart')[0]!.closest('li')).toHaveAttribute('aria-current', 'step');
  });

  it('celebrates a success with the time it took', () => {
    const p: UpdateProgressInfo = {
      ...running('done'), state: 'succeeded',
      status: { action: 'update', version: '0.12.0', to_version: '0.12.0', state: 'succeeded',
        started_at: '2026-11-14T15:00:00Z', finished_at: '2026-11-14T15:02:31Z' },
    };
    render(<UpdateProgress progress={p} current="0.12.0" offline={false} />);
    expect(screen.getByRole('status')).toHaveTextContent('Updated to v0.12.0 in 2:31');
  });

  it('marks the failed step and says it rolled back', () => {
    const p: UpdateProgressInfo = {
      ...running('health'), state: 'failed', failed_at: 'health', rolled_back: true,
      status: { action: 'update', version: '0.12.0', from_version: '0.11.0', state: 'failed',
        started_at: '2026-11-14T15:00:00Z', finished_at: '2026-11-14T15:03:00Z', rolled_back: 'yes' },
    };
    render(<UpdateProgress progress={p} current="0.11.0" offline={false} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Update failed at Health check; rolled back to v0.11.0');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('times from the click in this browser and suggests the host log when slow', () => {
    writePending({ to: '0.12.0', from: '0.11.0', action: 'update', at: T0 - 20 * 60_000 });
    render(<UpdateProgress progress={running('restart')} current="0.11.0" offline={false} />);
    expect(screen.getByLabelText('Time elapsed')).toHaveTextContent('21:00');
    expect(screen.getByText(/Taking a little longer than usual/)).toBeInTheDocument();
    expect(screen.getByText(/upgrade-agent.sh --status/)).toBeInTheDocument();
  });
});
