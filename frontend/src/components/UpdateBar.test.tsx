import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { UpdateSummary } from '../lib/api';
import { writePending } from '../lib/updateProgress';
import { UpdateBar } from './UpdateBar';

beforeEach(() => {
  document.title = 'NetWatch';
});
afterEach(() => {
  writePending(null);
  document.title = 'NetWatch';
});

const summary = (state: UpdateSummary['progress']['state']) =>
  ({ progress: { state, estimate_s: 200, request: { id: 'x', action: 'update', version: '0.12.0', requested_at: new Date().toISOString() } } }) as UpdateSummary;

describe('UpdateBar', () => {
  it('is hidden when nothing is updating', () => {
    const { container } = render(<MemoryRouter><UpdateBar summary={summary('idle')} offline={false} /></MemoryRouter>);
    expect(container).toBeEmptyDOMElement();
    expect(document.title).toBe('NetWatch');
  });

  it('shows a link to System and the percentage in the tab title while updating', () => {
    render(<MemoryRouter><UpdateBar summary={summary('requested')} offline={false} /></MemoryRouter>);
    const link = screen.getByRole('link', { name: /Update in progress, \d+%/ });
    expect(link).toHaveAttribute('href', '/system');
    expect(document.title).toMatch(/^\d+% · NetWatch$/);
  });

  it('keeps going while NetWatch restarts mid-update', () => {
    writePending({ to: '0.12.0', from: '0.11.0', action: 'update', at: Date.now() - 60_000 });
    render(<MemoryRouter><UpdateBar summary={null} offline /></MemoryRouter>);
    expect(screen.getByRole('link', { name: /Update in progress/ })).toBeInTheDocument();
  });
});
