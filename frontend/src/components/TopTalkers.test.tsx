import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TopTalkers } from './TopTalkers';

afterEach(() => vi.restoreAllMocks());

describe('TopTalkers', () => {
  it('shows live rates and switches to totals', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
      if (url === '/api/traffic/live') {
        return json({ generated_ms: 1, deco_api: true, as_of_ms: 1, down_bps: 25e6, up_bps: 1e6, by_deco: {},
          devices: [{ device_id: 7, label: 'Living TV', type: 'tv', macs: ['a'], down_bps: 24e6, up_bps: 5e5, deco_mac: 'x', deco_name: 'Office', connection: '5 GHz' }] });
      }
      if (url.startsWith('/api/traffic/top')) {
        return json({ hours: 24, down_bytes: 3e9, up_bytes: 1e8, devices: [{ device_id: 7, label: 'Living TV', type: 'tv', macs: ['a'], down_bytes: 3e9, up_bytes: 1e8, down_peak: 1, up_peak: 1 }] });
      }
      return json({});
    });
    render(<MemoryRouter><TopTalkers /></MemoryRouter>);
    expect(await screen.findByText('Living TV')).toBeInTheDocument();
    expect(screen.getByText(/24\.0 Mb\/s/)).toBeInTheDocument();
    expect(screen.getByText('Deco Office · 5 GHz')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '24 h' }));
    expect(await screen.findByText(/All devices: ↓ 2\.8 GB|All devices: ↓ 3\.0 GB/)).toBeInTheDocument();
  });
});
