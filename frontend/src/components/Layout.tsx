import { NavLink, Outlet } from 'react-router-dom';
import { api } from '../lib/api';
import { usePolling } from '../lib/usePolling';

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm ${isActive ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-white'}`;

export function Layout() {
  const { data: sys, error } = usePolling(api.system, 60_000);
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/60">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <div className="flex items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span className="font-semibold tracking-tight text-white">NetWatch</span>
          </div>
          <nav className="flex gap-1">
            <NavLink to="/" end className={link}>
              Overview
            </NavLink>
            <NavLink to="/targets" className={link}>
              Targets
            </NavLink>
          </nav>
          <div className="ml-auto text-xs text-slate-500">
            {error ? (
              <span className="text-rose-400">API unreachable</span>
            ) : sys ? (
              <span title={sys.default_route ? `via ${sys.default_route.interface}` : undefined}>
                v{sys.version}
                {sys.default_route && <> · gateway {sys.default_route.gateway}</>}
              </span>
            ) : null}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
