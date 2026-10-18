import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { api, ApiError, UNAUTHORIZED_EVENT } from '../lib/api';

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

type Phase = { kind: 'loading' } | { kind: 'error'; message: string; host?: boolean } | { kind: 'setup'; code: boolean } | { kind: 'login' } | { kind: 'ready'; username: string };

const input =
  'w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';

/** Shows first-run setup or the login form until there is a valid session, then the app. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });

  const refresh = useCallback(async () => {
    try {
      const st = await api.authStatus();
      if (st.setup_required) setPhase({ kind: 'setup', code: !!st.setup_code_required });
      else if (st.authenticated && st.username) setPhase({ kind: 'ready', username: st.username });
      else setPhase({ kind: 'login' });
    } catch (e) {
      setPhase({ kind: 'error', message: e instanceof Error ? e.message : String(e), host: e instanceof ApiError && e.status === 421 });
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
  if (phase.kind === 'error' && phase.host) return <UnknownHost />;
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
  return <CredentialsForm mode={phase.kind} needCode={phase.kind === 'setup' && phase.code} onDone={refresh} />;
}

/** Opened by a name NetWatch was not told about (it refuses those to block DNS rebinding). */
function UnknownHost() {
  const name = window.location.hostname;
  return (
    <Screen title="NetWatch">
      <div role="alert" className="space-y-3 text-sm text-slate-300">
        <p>
          NetWatch doesn't answer to the name <code className="text-amber-300">{name}</code> yet. It only accepts IP
          addresses and names you list, so other websites can't reach it through your browser.
        </p>
        <p>
          To use this name, add it to <code>NETWATCH_ALLOWED_HOSTS</code> in <code>.env</code> (comma-separated,
          keep your IP address too), then restart:
        </p>
        <pre className="overflow-x-auto rounded bg-slate-950 p-2 text-xs text-slate-200">{`NETWATCH_ALLOWED_HOSTS=<ip address>,${name}\nsudo docker compose up -d`}</pre>
        <p className="text-slate-500">Or open NetWatch by its IP address instead.</p>
      </div>
    </Screen>
  );
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

function CredentialsForm({ mode, needCode = false, onDone }: { mode: 'setup' | 'login'; needCode?: boolean; onDone: () => Promise<void> }) {
  const [username, setUsername] = useState(mode === 'setup' ? 'admin' : '');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [code, setCode] = useState('');
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
      if (setup) await api.setup(username, password, code);
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
        {needCode && (
          <div>
            <label htmlFor="auth-code" className="mb-1 block text-xs text-slate-400">Setup code</label>
            <input id="auth-code" className={input} autoComplete="off" required placeholder="abc123-def456-789abc" value={code} onChange={(e) => setCode(e.target.value)} />
            <p className="mt-1 text-[11px] text-slate-500">
              Printed by <code>setup.sh</code>; also in <code>data/setup-token</code> and <code>docker logs netwatch</code>.
            </p>
          </div>
        )}
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
