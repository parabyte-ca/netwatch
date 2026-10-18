import { useCallback, useEffect, useRef, useState } from 'react';

/** Calls `fn` immediately and then every `intervalMs` while the tab is visible.
 *
 * A timer tick is skipped while the previous request is still running (slow endpoints must not
 * pile up), and an answer that arrives after a newer request was started is ignored, so a slow
 * response for old `deps` (e.g. the previous time range) never overwrites a newer one. */
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const inFlight = useRef(0);

  const refresh = useCallback(async () => {
    const mine = ++seq.current;
    inFlight.current += 1;
    try {
      const value = await fnRef.current();
      if (mine === seq.current) {
        setData(value);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      inFlight.current -= 1;
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible' && inFlight.current === 0) void refresh();
    }, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      seq.current += 1; // answers to requests made for the old deps are now stale
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [intervalMs, refresh, ...deps]);

  return { data, error, refresh };
}
