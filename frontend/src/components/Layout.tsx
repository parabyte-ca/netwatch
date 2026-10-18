import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { api } from '../lib/api';
import { usePolling } from '../lib/usePolling';
import { AskPanel } from './AskPanel';
import { ErrorBoundary } from './ErrorBoundary';
import { useAuth } from './AuthGate';

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/health-score', label: 'Health' },
  { to: '/targets', label: 'Targets' },
  { to: '/devices', label: 'Devices' },
  { to: '/exposure', label: 'Exposure' },
  { to: '/timeline', label: 'Timeline' },
  { to: '/map', label: 'Map' },
  { to: '/diagnosis', label: 'Diagnosis' },
  { to: '/diagnostics', label: 'Diagnostics' },
  { to: '/system', label: 'System' },
  { to: '/admin', label: 'Admin' },
];

const link = ({ isActive }: { isActive: boolean }) =>
  `whitespace-nowrap rounded-md px-3 py-1.5 text-sm ${isActive ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-white'}`;

const mobileLink = ({ isActive }: { isActive: boolean }) =>
  `block rounded-md px-3 py-2.5 text-base ${isActive ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-800/60'}`;

export function Layout() {
  const { username, logout } = useAuth();
  const { data: sys, error } = usePolling(api.system, 60_000);
  const { data: upd } = usePolling(api.updates, 300_000);
  const busy = upd && (upd.progress.state === 'requested' || upd.progress.state === 'running');
  const [open, setOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);

  const badge = busy ? (
    <Link to="/system" className="whitespace-nowrap rounded-full bg-sky-900/60 px-2.5 py-1 text-xs text-sky-200">Updating…</Link>
  ) : upd?.update_available ? (
    <Link to="/system" className="whitespace-nowrap rounded-full bg-emerald-900/60 px-2.5 py-1 text-xs text-emerald-200 hover:bg-emerald-800/60">
      Update: v{upd.available[0]?.version}
    </Link>
  ) : null;
  const status = error ? (
    <span className="text-rose-400">API unreachable</span>
  ) : sys ? (
    <span title={sys.default_route ? `via ${sys.default_route.interface}` : undefined}>
      v{sys.version}
      {sys.default_route && <> · gateway {sys.default_route.gateway}</>}
    </span>
  ) : null;

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/60 print:hidden">
        <div className="mx-auto flex max-w-7xl items-center gap-x-6 px-4 py-3">
          <Link to="/" className="flex shrink-0 items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span className="font-semibold tracking-tight text-white">NetWatch</span>
          </Link>
          <nav className="hidden gap-1 lg:flex" aria-label="Main">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={link}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto hidden items-center gap-4 text-xs text-slate-500 lg:flex">
            {badge}
            {status}
            <span className="text-slate-400">{username}</span>
            <button onClick={() => void logout()} className="text-slate-400 hover:text-white">
              Sign out
            </button>
          </div>
          <div className="ml-auto flex items-center gap-3 lg:hidden">
            {badge}
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              aria-controls="mobile-menu"
              aria-label={open ? 'Close menu' : 'Open menu'}
              className="rounded-md p-2 text-slate-300 hover:bg-slate-800"
            >
              <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
              </svg>
            </button>
          </div>
        </div>
        {open && (
          <div id="mobile-menu" className="border-t border-slate-800 px-4 pb-4 pt-2 lg:hidden">
            <nav className="grid gap-1" aria-label="Main">
              {NAV.map((n) => (
                <NavLink key={n.to} to={n.to} end={n.end} className={mobileLink}>
                  {n.label}
                </NavLink>
              ))}
            </nav>
            <div className="mt-3 flex items-center justify-between border-t border-slate-800 pt-3 text-sm text-slate-400">
              <span className="truncate">{status}</span>
              <span className="flex shrink-0 items-center gap-4">
                <span>{username}</span>
                <button onClick={() => void logout()} className="text-slate-300 hover:text-white">
                  Sign out
                </button>
              </span>
            </div>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <ErrorBoundary key={location.pathname}>
          <Outlet />
        </ErrorBoundary>
        <ErrorBoundary label="Ask NetWatch" compact>
          <AskPanel />
        </ErrorBoundary>
      </main>
    </div>
  );
}
