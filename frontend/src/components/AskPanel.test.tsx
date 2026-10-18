import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AskPanel } from './AskPanel';

afterEach(() => vi.restoreAllMocks());

describe('AskPanel', () => {
  it('stays hidden without a key', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ available: false, model: 'x' })));
    const { container } = render(<AskPanel />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });

  it('asks a question and shows the answer with the data it used', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        expect(body.messages).toEqual([{ role: 'user', content: 'Which device uses the most bandwidth?' }]);
        return new Response(JSON.stringify({ answer: 'The living-room TV: 24 Mb/s.', tools: ['bandwidth', 'bandwidth'], model: 'claude-opus-5-5', usage: {}, stop_reason: 'end_turn' }));
      }
      return new Response(JSON.stringify({ available: true, model: 'claude-opus-5-5' }));
    });
    render(<AskPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ask NetWatch' }));
    fireEvent.click(screen.getByRole('button', { name: 'Which device uses the most bandwidth?' }));
    expect(await screen.findByText('The living-room TV: 24 Mb/s.')).toBeInTheDocument();
    expect(screen.getByText('Looked at: bandwidth')).toBeInTheDocument();
    expect(f).toHaveBeenCalledWith('/api/ask', expect.objectContaining({ method: 'POST' }));
  });
});
