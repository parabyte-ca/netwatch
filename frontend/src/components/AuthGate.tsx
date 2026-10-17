import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { api, UNAUTHORIZED_EVENT } from '../lib/api';

interface AuthContextValue {
  username: string;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthGate>');
  return ctx;
}

type Phase = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'setup' } | { kind: 'login' } | { kind: 'ready'; username: string };

const input =
  'w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';

/** Shows first-run setup or the login form until there is a valid session, then the app. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });

  const refresh = useCallback(async () => {
    try {
      const st = await api.authStatus();
      if (st.setup_required) setPhase({ kind: 'setup' });
      else if (st.authenticated && st.username) setPhase({ kind: 'ready', username: st.username });
      else setPhase({ kind: 'login' });
    } catch (e) {
      setPhase({ kind: 'error', message: e instanceof Error ? e.message : String(e) });
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onUnauthorized = () => setPhase((p) => (p.kind === 'ready' ? { kind: 'login' } : p));
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [refresh]);

  const logout = useCallback(async () => {
    await api.logout().catch(() => undefined);
    setPhase({ kind: 'login' });
  }, []);

  if (phase.kind === 'ready') {
    return <AuthContext.Provider value={{ username: phase.username, logout }}>{children}</AuthContext.Provider>;
  }
  if (phase.kind === 'loading') return <Screen title="NetWatch"><p className="text-sm text-slate-500">Loading…</p></Screen>;
  if (phase.kind === 'error') {
    return (
      <Screen title="NetWatch">
        <p className="text-sm text-rose-300">Cannot reach the server: {phase.message}</p>
        <button onClick={() => void refresh()} className="mt-4 text-sm text-sky-400 hover:underline">
          Retry
        </button>
      </Screen>
    );
  }
  return <CredentialsForm mode={phase.kind} onDone={refresh} />;
}

function Screen({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl bg-slate-900 p-6 ring-1 ring-slate-800">
        <div className="mb-5 flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <h1 className="text-lg font-semibold text-white">{title}</h1>
        </div>
        {subtitle && <p className="mb-4 text-sm text-slate-400">{subtitle}</p>}
        {children}
      </div>
    </div>
  );
}

function CredentialsForm({ mode, onDone }: { mode: 'setup' | 'login'; onDone: () => Promise<void> }) {
  const [username, setUsername] = useState(mode === 'setup' ? 'admin' : '');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const setup = mode === 'setup';

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (setup && password !== confirm) {
      setError('Passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      if (setup) await api.setup(username, password);
      else await api.login(username, password);
      await onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen
      title={setup ? 'Welcome to NetWatch' : 'Sign in to NetWatch'}
      subtitle={setup ? 'Create the admin account. You will use it to sign in from now on.' : undefined}
    >
      <form onSubmit={submit} className="space-y-3" aria-label={setup ? 'Create admin account' : 'Sign in'}>
        <div>
          <label htmlFor="auth-user" className="mb-1 block text-xs text-slate-400">Username</label>
          <input id="auth-user" className={input} autoComplete="username" required value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div>
          <label htmlFor="auth-pass" className="mb-1 block text-xs text-slate-400">Password</label>
          <input id="auth-pass" className={input} type="password" autoComplete={setup ? 'new-password' : 'current-password'} required minLength={setup ? 10 : 1} value={password} onChange={(e) => setPassword(e.target.value)} />
          {setup && <p className="mt-1 text-[11px] text-slate-500">At least 10 characters.</p>}
        </div>
        {setup && (
          <div>
            <label htmlFor="auth-confirm" className="mb-1 block text-xs text-slate-400">Confirm password</label>
            <input id="auth-confirm" className={input} type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </div>
        )}
        {error && <p role="alert" className="rounded-md bg-rose-950/60 px-3 py-2 text-sm text-rose-300">{error}</p>}
        <button type="submit" disabled={busy} className="w-full rounded-md bg-sky-600 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50">
          {busy ? 'Please wait…' : setup ? 'Create account' : 'Sign in'}
        </button>
      </form>
    </Screen>
  );
}
