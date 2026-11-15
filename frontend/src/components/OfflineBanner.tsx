import { useEffect, useState } from 'react';
import { OFFLINE_EVENT, ONLINE_EVENT } from '../lib/api';

/** A strip at the top while NetWatch cannot be reached (phone off the home network, NetWatch down). */
export function OfflineBanner({ hidden = false }: { hidden?: boolean }) {
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    const off = () => setOffline(true);
    const on = () => setOffline(false);
    window.addEventListener(OFFLINE_EVENT, off);
    window.addEventListener(ONLINE_EVENT, on);
    return () => {
      window.removeEventListener(OFFLINE_EVENT, off);
      window.removeEventListener(ONLINE_EVENT, on);
    };
  }, []);
  if (!offline || hidden) return null;
  return (
    <div role="status" className="bg-amber-900/70 px-4 py-2 text-center text-sm text-amber-100 print:hidden">
      No connection to NetWatch. Pages will refresh when it is back.
    </div>
  );
}
