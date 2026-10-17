import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { UpdateSummary } from '../lib/api';
import { AuthGate } from '../components/AuthGate';
import { ReleaseNotes, System } from './System';

afterEach(() => vi.restoreAllMocks());

const base: UpdateSummary = {
  current: '0.2.0',
  latest: '0.3.0',
  update_available: true,
  latest_kind: 'minor',
  available: [
    { version: '0.3.0', tag: 'v0.3.0', name: 'v0.3.0', notes: '### Added\n- Outage timeline', published_at: null, url: null, kind: 'minor' },
    { version: '0.2.1', tag: 'v0.2.1', name: 'v0.2.1', notes: '### Fixed\n- A bug', published_at: null, url: null, kind: 'patch' },
  ],
  auto_target: '0.2.1',
  checked_at: 1_760_000_000,
  check_error: null,
  check_enabled: true,
  policy: { mode: 'patch', window: '03:30' },
  timezone: 'America/Toronto',
  in_app_updates: true,
  helper: { connected: true, last_seen: '2026-10-17T12:00:00Z' },
  progress: { state: 'idle' },
  history: [],
  rollback_available: false,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockApi(summary: UpdateSummary, onApply?: (body: unknown) => void) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === '/api/auth/status') return json({ setup_required: false, authenticated: true, username: 'admin' });
    if (url === '/api/updates') return json(summary);
    if (url === '/api/updates/apply') {
      onApply?.(JSON.parse(String(init?.body)));
      return json({ id: 'x' }, 202);
    }
    if (url === '/api/updates/log') return new Response('');
    return json({}, 404);
  });
}

function renderSystem() {
  return render(<AuthGate><System /></AuthGate>);
}

describe('System page', () => {
  it('lists updates, labels them, and installs the newest after confirmation', async () => {
    const applied: unknown[] = [];
    mockApi(base, (b) => applied.push(b));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderSystem();
    expect(await screen.findByText('Features')).toBeInTheDocument();
    expect(screen.getByText('Bug fix')).toBeInTheDocument();
    expect(screen.getByText(/Installs automatically at 03:30/)).toBeInTheDocument();
    expect(screen.getByText('Outage timeline')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Install v0.3.0' }));
    await waitFor(() => expect(applied).toEqual([{ version: '0.3.0' }]));
    expect(await screen.findByRole('alert')).toHaveTextContent('Update to v0.3.0 requested');
  });

  it('disables installs and explains when the agent is not connected', async () => {
    mockApi({ ...base, helper: { connected: false, last_seen: null } });
    renderSystem();
    expect(await screen.findByText(/Not connected/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Install v0.3.0' })).toBeDisabled();
  });

  it('shows a failed, rolled-back update', async () => {
    mockApi({
      ...base,
      progress: { state: 'failed', status: { action: 'update', version: '0.3.0', state: 'failed', rolled_back: 'yes' } },
    });
    renderSystem();
    expect(await screen.findByText(/failed and was rolled back automatically/)).toBeInTheDocument();
  });
});

describe('ReleaseNotes', () => {
  it('renders headings and bullets', () => {
    render(<ReleaseNotes text={'### Fixed\n- One\n- Two\nPlain line'} />);
    expect(screen.getByText('Fixed').tagName).toBe('H4');
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('Plain line').tagName).toBe('P');
  });
});
