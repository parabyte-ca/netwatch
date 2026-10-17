import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DiagnosisResponse, Report as ReportData } from '../lib/api';
import { Diagnosis } from './Diagnosis';
import { Report } from './Report';

afterEach(() => vi.restoreAllMocks());

const now = Date.now();
const cause = {
  id: 'isp', title: 'Bell network outage (beyond the Home Hub)', layer: 'isp', confidence: 93,
  evidence: ['Failing: Cloudflare, Google.', 'The Home Hub kept answering (Gateway).'],
  next_steps: ['Note the times and include this report when you contact Bell.'],
};
const diagnosis: DiagnosisResponse = {
  now_ms: now,
  topology: [
    { layer: 'host', parent: null, label: 'TrueNAS network link', state: 'up', inferred: false, members: [{ name: 'enp3s0', state: 'up' }] },
    { layer: 'lan', parent: 'host', label: 'Switch and cabling', state: 'up', inferred: true, members: [] },
    { layer: 'gateway', parent: 'lan', label: 'Bell Home Hub', state: 'up', inferred: false, members: [{ target_id: 1, name: 'Gateway', host: '192.168.2.1', state: 'up' }] },
    { layer: 'isp', parent: 'gateway', label: 'Bell network', state: 'down', inferred: true, members: [] },
  ],
  patterns: [{ kind: 'interval', title: 'Failures repeat about every 6 h', hint: 'A fixed interval points at something scheduled.' }],
  incidents: [{
    id: 'i1', start_ms: now - 3_600_000, end_ms: now - 3_480_000, open: false, duration_ms: 120_000, event_ids: [7, 8],
    severity: 'critical', failing: [{ target_id: 2, name: 'Cloudflare', host: '1.1.1.1', layer: 'internet' }],
    healthy: [{ target_id: 1, name: 'Gateway', host: '192.168.2.1', layer: 'gateway' }], failing_layers: ['internet'],
    nic_events: [], anomalies: [], path_tests: [], causes: [cause],
  }],
};

describe('Diagnosis', () => {
  it('shows the map, patterns and an expandable diagnosed incident', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(diagnosis), { status: 200 }));
    const { container } = render(<MemoryRouter><Diagnosis /></MemoryRouter>);
    expect(await screen.findByText('Bell Home Hub')).toBeInTheDocument();
    expect(screen.getByText('Failures repeat about every 6 h')).toBeInTheDocument();
    expect(screen.getByText('93 %')).toBeInTheDocument();
    expect(screen.queryByText('Next steps')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Bell network outage/ }));
    expect(screen.getByText('Next steps')).toBeInTheDocument();
    expect(screen.getByText('The Home Hub kept answering (Gateway).')).toBeInTheDocument();
    // the most likely cause's layer is highlighted on the map
    expect(container.querySelector('[data-layer="isp"]')?.className).toContain('ring-rose');
    expect(container.querySelector('[data-layer="gateway"]')?.className).not.toContain('ring-rose');
  });
});

describe('Report', () => {
  it('renders the report and offers Markdown download and printing', async () => {
    const report: ReportData = {
      generated_at: '2026-10-17 15:00:00 EDT', version: '0.5.0',
      period: { start: '2026-09-17 15:00:00 EDT', end: '2026-10-17 15:00:00 EDT', start_ms: 0, end_ms: 1 },
      scope: 'isp', gateway: '192.168.2.1',
      summary: { incidents: 1, isp_incidents: 1, isp_downtime: '2 min 00 s', isp_availability_pct: 99.995, longest: '2 min 00 s' },
      patterns: [], targets: [{ name: 'Cloudflare', host: '1.1.1.1', layer_label: 'Internet destinations', probes: 1000, loss_pct: 0.4, median_ms: 3.2, p95_ms: 5.1 }],
      speeds: [], ruled_out: ['The TrueNAS network link stayed up with no link drops or interface errors.'],
      incidents: [{ ...diagnosis.incidents[0]!, start: '2026-10-17 14:00:00 EDT', end: '2026-10-17 14:02:00 EDT', duration: '2 min 00 s',
        cause: cause.title, confidence: 93,
        path_hops: [{ host: '1.1.1.1', ts: '2026-10-17 14:00:10 EDT', first_loss_hop: 3,
          hops: [{ hop: 1, host: '192.168.2.1', loss_pct: 0, avg: 0.6 }, { hop: 3, host: '64.230.1.1', loss_pct: 100, avg: null }] }] }],
      markdown: '# Network incident report',
    };
    const urls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      urls.push(String(input));
      return new Response(JSON.stringify(report), { status: 200 });
    });
    const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    render(<MemoryRouter><Report /></MemoryRouter>);
    expect(await screen.findByText('Network incident report')).toBeInTheDocument();
    expect(screen.getAllByText(cause.title).length).toBeGreaterThan(0);
    expect(screen.getByText('64.230.1.1').closest('tr')?.className).toContain('text-rose');
    expect(urls[0]).toContain('scope=isp');
    expect(screen.getByRole('link', { name: 'Download Markdown' }).getAttribute('href')).toContain('format=md');
    fireEvent.click(screen.getByRole('button', { name: /Print/ }));
    expect(print).toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Only incidents pointing at Bell'));
    await waitFor(() => expect(urls.at(-1)).toContain('scope=all'));
  });
});
