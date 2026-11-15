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

/** "Install app" next to Sign out: hidden when installed or when the browser can't install. */
export function InstallButton({ className = '' }: { className?: string }) {
  const [prompt, setPrompt] = useState<InstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(isStandalone);
  const [help, setHelp] = useState(false);

  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setPrompt(e as InstallPromptEvent);
    };
    const onInstalled = () => {
      setInstalled(true);
      setPrompt(null);
    };
    window.addEventListener('beforeinstallprompt', onPrompt);
    window.addEventListener('appinstalled', onInstalled);
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt);
      window.removeEventListener('appinstalled', onInstalled);
    };
  }, []);

  if (installed) return null;
  if (prompt) {
    return (
      <button
        type="button"
        className={className}
        onClick={async () => {
          await prompt.prompt();
          const { outcome } = await prompt.userChoice;
          if (outcome === 'accepted') setInstalled(true);
          setPrompt(null);
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
