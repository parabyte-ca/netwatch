import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api, type AdGuardActionsState, type UpstreamTest } from '../lib/api';
import { usePolling } from '../lib/usePolling';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const input = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const PAUSES = [
  { minutes: 5, label: '5 min' },
  { minutes: 15, label: '15 min' },
  { minutes: 30, label: '30 min' },
  { minutes: 60, label: '1 hour' },
  { minutes: 480, label: '8 hours' },
];

const clock = (ms: number) => new Date(ms).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' });
const code = (s: string) => <code className="break-all rounded bg-slate-800 px-1 font-mono text-xs">{s}</code>;

export function ruleConfirm(domain: string, kind: 'allow' | 'block', run: () => Promise<void>): Confirm {
  const rule = kind === 'allow' ? `@@||${domain}^` : `||${domain}^`;
  return {
    title: kind === 'allow' ? `Allow ${domain}?` : `Block ${domain}?`,
    body: (
      <>
        <p>
          Adds {code(rule)} to AdGuard Home's custom rules, so {domain} and its subdomains are{' '}
          {kind === 'allow' ? 'never blocked' : 'blocked'} for every device.
        </p>
        <p className="text-xs text-slate-400">NetWatch keeps track of this rule, so you can remove it from the DNS page later.</p>
      </>
    ),
    confirmLabel: kind === 'allow' ? 'Allow' : 'Block',
    danger: kind === 'block',
    run,
  };
}

/** Allow or block a domain from any list of domains, after confirming. Only offered while
 * "AdGuard Home actions" is turned on. */
export function useRuleAction(onDone?: () => void) {
  const { data, refresh } = usePolling(api.adguardActions, 60_000);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const request = (domain: string, kind: 'allow' | 'block') =>
    setAsk(ruleConfirm(domain, kind, async () => {
      await api.adguardAddRule(domain, kind);
      await refresh();
      onDone?.();
    }));
  const dialog = ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />;
  return { enabled: !!data?.enabled, request, dialog };
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2 rounded-md bg-slate-950 p-3 ring-1 ring-slate-800">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      {children}
    </div>
  );
}

/** Pause protection, allow or block domains, switch the upstream DNS. Every change asks first and
 * is written to the security log by the server. */
export function AdGuardControls({ protection, onChanged }: { protection: boolean | null | undefined; onChanged: () => void }) {
  const { data, error, refresh } = usePolling(api.adguardActions, 15_000);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [domain, setDomain] = useState('');
  const [preset, setPreset] = useState('');
  const [custom, setCustom] = useState('');
  const [test, setTest] = useState<UpstreamTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);

  if (!data) return error ? <p className="text-sm text-rose-400">{error}</p> : null;
  if (!data.enabled) {
    return (
      <p className="text-sm text-slate-400">
        NetWatch only reads AdGuard Home. To pause protection, allow or block domains or switch the upstream DNS from here, turn on{' '}
        <Link to="/admin" className="text-sky-400 hover:underline">Admin → settings → AdGuard Home actions</Link>.
      </p>
    );
  }
  const done = async () => {
    await refresh();
    onChanged();
  };
  const s: AdGuardActionsState = data;
  const chosen = preset === 'custom'
    ? custom.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean)
    : (s.presets.find((p) => p.name === preset)?.upstreams ?? []);
  const tested = test && test.ok && test.upstreams.join() === chosen.join();

  const pause = (minutes: number, label: string) => {
    const until = clock(Date.now() + minutes * 60_000);
    setAsk({
      title: `Pause AdGuard Home protection for ${label}?`,
      body: (
        <>
          <p>Ads, trackers and everything else AdGuard Home blocks will get through for every device until {until}.</p>
          <p className="text-xs text-slate-400">NetWatch turns protection back on by itself at {until}, even if it restarts in the meantime.</p>
        </>
      ),
      confirmLabel: `Pause for ${label}`,
      danger: true,
      run: async () => {
        await api.adguardPause(minutes);
        await done();
      },
    });
  };
  const runTest = async () => {
    setTesting(true);
    setTestError(null);
    setTest(null);
    try {
      setTest(await api.adguardTestUpstreams(chosen));
    } catch (e) {
      setTestError(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  };
  const switchTo = (list: string[], restore: boolean) =>
    setAsk({
      title: restore ? 'Switch back to the previous upstream DNS?' : 'Switch the upstream DNS?',
      body: (
        <>
          <p>AdGuard Home will send the network's DNS queries to:</p>
          <ul className="list-disc pl-5">{list.map((u) => <li key={u}>{code(u)}</li>)}</ul>
          {s.upstreams && <p className="text-xs text-slate-400">Instead of: {s.upstreams.join(', ')}. You can switch back here.</p>}
          {!restore && <p className="text-xs text-emerald-300">AdGuard Home's test passed for each server.</p>}
        </>
      ),
      confirmLabel: 'Switch',
      run: async () => {
        await (restore ? api.adguardRestoreUpstreams() : api.adguardSwitchUpstreams(list));
        setTest(null);
        await done();
      },
    });

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      <Block title="Protection">
        <p className="text-sm" aria-label="Protection state">
          {s.pause ? (
            <span className="text-amber-300">Paused until {clock(s.pause.until_ms)} (by {s.pause.by} from NetWatch)</span>
          ) : protection === false ? (
            <span className="text-rose-300">Off</span>
          ) : (
            <span className="text-emerald-300">On</span>
          )}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {PAUSES.map((p) => (
            <button key={p.minutes} className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => pause(p.minutes, p.label)}>
              Pause {p.label}
            </button>
          ))}
        </div>
        {(s.pause || protection === false) && (
          <button className={`${btn} bg-emerald-600 text-white hover:bg-emerald-500`}
            onClick={() => setAsk({
              title: 'Turn protection back on?',
              body: <p>AdGuard Home starts blocking again for every device.</p>,
              confirmLabel: 'Turn on',
              run: async () => {
                await api.adguardResume();
                await done();
              },
            })}>
            Resume now
          </button>
        )}
      </Block>

      <Block title="Allow or block a domain">
        <form className="flex flex-wrap gap-1.5" onSubmit={(e) => e.preventDefault()}>
          <input aria-label="Domain" className={`${input} min-w-0 flex-1`} placeholder="example.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
          <button type="button" disabled={!domain.trim()} className={`${btn} bg-emerald-700 text-white hover:bg-emerald-600`}
            onClick={() => setAsk(ruleConfirm(domain.trim().toLowerCase(), 'allow', async () => { await api.adguardAddRule(domain, 'allow'); setDomain(''); await done(); }))}>
            Allow
          </button>
          <button type="button" disabled={!domain.trim()} className={`${btn} bg-rose-700 text-white hover:bg-rose-600`}
            onClick={() => setAsk(ruleConfirm(domain.trim().toLowerCase(), 'block', async () => { await api.adguardAddRule(domain, 'block'); setDomain(''); await done(); }))}>
            Block
          </button>
        </form>
        {s.rules.length > 0 ? (
          <ul className="divide-y divide-slate-800" aria-label="Rules added by NetWatch">
            {s.rules.map((r) => (
              <li key={r.rule} className="flex items-center justify-between gap-2 py-1">
                <span className="min-w-0 truncate text-xs">
                  <span className={r.kind === 'allow' ? 'text-emerald-300' : 'text-rose-300'}>{r.kind === 'allow' ? 'Allowed' : 'Blocked'}</span>{' '}
                  <span className="font-mono text-slate-200">{r.domain}</span>
                </span>
                <button className="shrink-0 text-xs text-slate-400 hover:text-white"
                  onClick={() => setAsk({
                    title: `Remove the rule for ${r.domain}?`,
                    body: <p>Removes {code(r.rule)} from AdGuard Home's custom rules. Your own rules are not touched.</p>,
                    confirmLabel: 'Remove',
                    run: async () => {
                      await api.adguardRemoveRule(r.rule);
                      await done();
                    },
                  })}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-slate-500">No rules added from NetWatch.</p>
        )}
      </Block>

      <Block title="Upstream DNS">
        <p className="break-all text-xs text-slate-300" aria-label="Current upstreams">
          Now: {s.upstreams ? s.upstreams.join(', ') : 'unknown (AdGuard Home did not answer)'}
        </p>
        <select aria-label="Upstream preset" className={`${input} w-full`} value={preset} onChange={(e) => { setPreset(e.target.value); setTest(null); }}>
          <option value="">Choose…</option>
          {s.presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
          <option value="custom">Custom…</option>
        </select>
        {preset === 'custom' && (
          <textarea aria-label="Custom upstreams" rows={2} className={`${input} w-full font-mono text-xs`} placeholder="tls://dns.example.net, 9.9.9.9"
            value={custom} onChange={(e) => { setCustom(e.target.value); setTest(null); }} />
        )}
        <div className="flex flex-wrap gap-1.5">
          <button className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`} disabled={!chosen.length || testing} onClick={() => void runTest()}>
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!tested} onClick={() => switchTo(chosen, false)}
            title={tested ? undefined : 'Test first'}>
            Switch
          </button>
          {s.previous_upstreams && (
            <button className={`${btn} text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => switchTo(s.previous_upstreams!.upstreams, true)}>
              Switch back
            </button>
          )}
        </div>
        {testError && <p className="text-xs text-rose-300">{testError}</p>}
        {test && (
          <ul className="space-y-0.5 text-xs" aria-label="Upstream test">
            {Object.entries(test.results).map(([u, r]) => (
              <li key={u} className={r === 'OK' ? 'text-emerald-300' : 'text-rose-300'}>
                {r === 'OK' ? '✓' : '✗'} <span className="font-mono">{u}</span>{r === 'OK' ? '' : `: ${r}`}
              </li>
            ))}
          </ul>
        )}
      </Block>
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </div>
  );
}
