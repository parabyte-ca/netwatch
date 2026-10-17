import { Link, NavLink, Outlet } from 'react-router-dom';
import { api } from '../lib/api';
import { usePolling } from '../lib/usePolling';
import { useAuth } from './AuthGate';

const link = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm ${isActive ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-white'}`;

export function Layout() {
  const { username, logout } = useAuth();
  const { data: sys, error } = usePolling(api.system, 60_000);
  const { data: upd } = usePolling(api.updates, 300_000);
  const busy = upd && (upd.progress.state === 'requested' || upd.progress.state === 'running');
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/60 print:hidden">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
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
            <NavLink to="/devices" className={link}>
              Devices
            </NavLink>
            <NavLink to="/timeline" className={link}>
              Timeline
            </NavLink>
            <NavLink to="/diagnosis" className={link}>
              Diagnosis
            </NavLink>
            <NavLink to="/diagnostics" className={link}>
              Diagnostics
            </NavLink>
            <NavLink to="/system" className={link}>
              System
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-4 text-xs text-slate-500">
            {busy ? (
              <Link to="/system" className="rounded-full bg-sky-900/60 px-2.5 py-1 text-sky-200">Updating…</Link>
            ) : upd?.update_available ? (
              <Link to="/system" className="rounded-full bg-emerald-900/60 px-2.5 py-1 text-emerald-200 hover:bg-emerald-800/60">
                Update available: v{upd.available[0]?.version}
              </Link>
            ) : null}
            {error ? (
              <span className="text-rose-400">API unreachable</span>
            ) : sys ? (
              <span title={sys.default_route ? `via ${sys.default_route.interface}` : undefined}>
                v{sys.version}
                {sys.default_route && <> · gateway {sys.default_route.gateway}</>}
              </span>
            ) : null}
            <span className="text-slate-400">{username}</span>
            <button onClick={() => void logout()} className="text-slate-400 hover:text-white">
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
