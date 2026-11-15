import { useEffect, useState } from 'react';

/** Chrome, Edge and Android: the browser's own install prompt, kept until the user asks. */
interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

export function isStandalone(): boolean {
  return (
    window.matchMedia?.('(display-mode: standalone)').matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

export function isIos(ua = navigator.userAgent, touch = navigator.maxTouchPoints): boolean {
  // iPadOS reports itself as a Mac; a Mac with a touch screen is an iPad.
  return /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touch > 1);
}

// The browser offers installing once, often before the app has signed in and drawn its menu, so
// the offer is kept here for every Install button (desktop header and phone menu alike).
let offer: InstallPromptEvent | null = null;
let installedNow = false;
const subscribers = new Set<() => void>();
const notify = () => subscribers.forEach((f) => f());
let listening = false;

/** Called once at startup (main.tsx), before React draws anything. */
export function listenForInstallPrompt(): void {
  if (listening) return;
  listening = true;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    offer = e as InstallPromptEvent;
    notify();
  });
  window.addEventListener('appinstalled', () => {
    installedNow = true;
    offer = null;
    notify();
  });
}

/** For tests: forget a kept offer. */
export function resetInstallPrompt(): void {
  offer = null;
  installedNow = false;
  notify();
}

/** "Install app" next to Sign out: hidden when installed or when the browser can't install. */
export function InstallButton({ className = '' }: { className?: string }) {
  const [, rerender] = useState(0);
  const [help, setHelp] = useState(false);

  useEffect(() => {
    listenForInstallPrompt();
    const f = () => rerender((n) => n + 1);
    subscribers.add(f);
    return () => {
      subscribers.delete(f);
    };
  }, []);

  if (installedNow || isStandalone()) return null;
  if (offer) {
    const current = offer;
    return (
      <button
        type="button"
        className={className}
        onClick={async () => {
          await current.prompt();
          const { outcome } = await current.userChoice;
          if (outcome === 'accepted') installedNow = true;
          offer = null; // a prompt can only be shown once
          notify();
        }}
      >
        Install app
      </button>
    );
  }
  // Safari on iPhone and iPad has no prompt; installing needs HTTPS there too.
  if (isIos() && window.isSecureContext) {
    return (
      <span className="relative">
        <button type="button" className={className} aria-expanded={help} onClick={() => setHelp((h) => !h)}>
          Install app
        </button>
        {help && (
          <span role="note" className="absolute right-0 top-full z-20 mt-2 block w-60 rounded-md border border-slate-700 bg-slate-900 p-3 text-left text-sm text-slate-200 shadow-lg">
            In Safari, tap Share, then Add to Home Screen.
          </span>
        )}
      </span>
    );
  }
  return null;
}
