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

describe('AskPanel tray', () => {
  const available = () =>
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ available: true, model: 'claude-opus-5-5' })));
  const tray = () => screen.getByLabelText('Ask NetWatch', { selector: 'aside' });
  const closed = () => {
    expect(tray()).toHaveAttribute('inert');
    expect(tray()).toHaveAttribute('aria-hidden', 'true');
    expect(tray().className).toContain('translate-y-[calc(100%+2rem)]');
  };
  const open = () => {
    expect(tray()).not.toHaveAttribute('inert');
    expect(tray()).toHaveAttribute('aria-hidden', 'false');
    expect(tray().className).toContain('translate-y-0');
  };

  it('stays mounted and slides: closed is off-screen and out of reach, open is in view', async () => {
    available();
    render(<AskPanel />);
    const launcher = await screen.findByRole('button', { name: 'Ask NetWatch' });
    closed();
    expect(tray().className).toContain('transition-[transform,visibility]');
    expect(tray().className).toContain('motion-reduce:transition-none');
    fireEvent.click(launcher);
    open();
    expect(screen.getByLabelText('Question')).toHaveFocus();
    expect(launcher).toHaveAttribute('inert'); // the launcher fades out while the tray is open
  });

  it('slides down when you tap away, but not for taps inside the tray', async () => {
    available();
    render(
      <>
        <p>page behind</p>
        <AskPanel />
      </>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Ask NetWatch' }));
    open();
    fireEvent.pointerDown(screen.getByLabelText('Question'));
    fireEvent.pointerDown(screen.getByText('Ask about outages, slowness, devices or security. Answers come from NetWatch\'s own data.'));
    open();
    fireEvent.pointerDown(screen.getByText('page behind'));
    closed();
    // And it opens again from the launcher.
    fireEvent.click(screen.getByRole('button', { name: 'Ask NetWatch' }));
    open();
  });

  it('closes with Esc or the close button and returns focus to the launcher', async () => {
    available();
    render(<AskPanel />);
    const launcher = await screen.findByRole('button', { name: 'Ask NetWatch' });
    fireEvent.click(launcher);
    fireEvent.keyDown(document, { key: 'Escape' });
    closed();
    expect(launcher).toHaveFocus();
    fireEvent.click(launcher);
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    closed();
  });

  it('keeps the conversation and the draft while it is slid away', async () => {
    available();
    render(<AskPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ask NetWatch' }));
    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'half a question' } });
    fireEvent.keyDown(document, { key: 'Escape' });
    closed();
    fireEvent.click(screen.getByRole('button', { name: 'Ask NetWatch' }));
    expect(screen.getByLabelText('Question')).toHaveValue('half a question');
  });

  it('does not listen for taps while closed', async () => {
    available();
    const add = vi.spyOn(document, 'addEventListener');
    render(<AskPanel />);
    await screen.findByRole('button', { name: 'Ask NetWatch' });
    expect(add.mock.calls.some(([type]) => type === 'pointerdown')).toBe(false);
  });
});
