import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InstallButton, isIos } from './InstallButton';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function installPrompt(outcome: 'accepted' | 'dismissed') {
  const e = new Event('beforeinstallprompt', { cancelable: true }) as Event & Record<string, unknown>;
  e.prompt = vi.fn().mockResolvedValue(undefined);
  e.userChoice = Promise.resolve({ outcome });
  return e;
}

describe('InstallButton', () => {
  it('is hidden when the browser offers no way to install', () => {
    const { container } = render(<InstallButton />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the browser prompt and hides itself once installed', async () => {
    render(<InstallButton />);
    const e = installPrompt('accepted');
    act(() => {
      window.dispatchEvent(e);
    });
    expect(e.defaultPrevented).toBe(true);
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Install app' })));
    expect(e.prompt).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Install app' })).not.toBeInTheDocument();
  });

  it('is hidden when already running as an installed app', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true } as MediaQueryList);
    render(<InstallButton />);
    act(() => {
      window.dispatchEvent(installPrompt('accepted'));
    });
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('explains Add to Home Screen on an iPhone over HTTPS', () => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)');
    vi.stubGlobal('isSecureContext', true);
    render(<InstallButton />);
    fireEvent.click(screen.getByRole('button', { name: 'Install app' }));
    expect(screen.getByRole('note')).toHaveTextContent('tap Share, then Add to Home Screen');
  });

  it('recognises an iPad that reports itself as a Mac', () => {
    expect(isIos('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 5)).toBe(true);
    expect(isIos('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 0)).toBe(false);
  });
});
