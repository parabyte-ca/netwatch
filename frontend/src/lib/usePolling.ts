import { useCallback, useEffect, useRef, useState } from 'react';

/** Calls `fn` immediately and then every `intervalMs` while the tab is visible. */
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const refresh = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    const start = () => {
      void refresh();
      timer = setInterval(() => {
        if (document.visibilityState === 'visible') void refresh();
      }, intervalMs);
    };
    start();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [intervalMs, refresh, ...deps]);

  return { data, error, refresh };
}
