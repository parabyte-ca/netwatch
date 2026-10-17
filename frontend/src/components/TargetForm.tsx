import { useState, type FormEvent } from 'react';
import type { Group, Kind, TargetInput } from '../lib/api';
import { GROUP_LABEL, GROUP_ORDER } from '../lib/format';

const KIND_HELP: Record<Kind, string> = {
  icmp: 'Ping (ICMP echo). Interval 1 s or more.',
  dns: 'Query a resolver and time the answer. Host is the resolver IP.',
  tcp: 'Time a TCP connection to host:port.',
  http: 'GET a URL; success is a status below 400 (or the expected status).',
};

const DEFAULT_INTERVAL: Record<Kind, number> = { icmp: 2, dns: 10, tcp: 30, http: 60 };

export const EMPTY_TARGET: TargetInput = {
  name: '',
  kind: 'icmp',
  host: '',
  interval_s: 2,
  enabled: true,
  group: 'custom',
};

const input =
  'w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const label = 'mb-1 block text-xs font-medium text-slate-400';

interface Props {
  initial: TargetInput;
  submitLabel: string;
  onSubmit: (value: TargetInput) => Promise<void>;
  onCancel?: () => void;
}

function numOrNull(v: string): number | null {
  return v.trim() === '' ? null : Number(v);
}

export function TargetForm({ initial, submitLabel, onSubmit, onCancel }: Props) {
  const [v, setV] = useState<TargetInput>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof TargetInput>(k: K, val: TargetInput[K]) => setV((p) => ({ ...p, [k]: val }));

  const changeKind = (kind: Kind) =>
    setV((p) => ({ ...p, kind, interval_s: p.kind === kind ? p.interval_s : DEFAULT_INTERVAL[kind], timeout_s: null }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(v);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label="Target form">
      <div className="sm:col-span-2">
        <label className={label} htmlFor="t-name">Name</label>
        <input id="t-name" className={input} required maxLength={64} value={v.name} onChange={(e) => set('name', e.target.value)} />
      </div>
      <div>
        <label className={label} htmlFor="t-kind">Probe</label>
        <select id="t-kind" className={input} value={v.kind} onChange={(e) => changeKind(e.target.value as Kind)}>
          <option value="icmp">ICMP ping</option>
          <option value="dns">DNS query</option>
          <option value="tcp">TCP connect</option>
          <option value="http">HTTP check</option>
        </select>
      </div>
      <div>
        <label className={label} htmlFor="t-group">Group</label>
        <select id="t-group" className={input} value={v.group} onChange={(e) => set('group', e.target.value as Group)}>
          {GROUP_ORDER.map((g) => (
            <option key={g} value={g}>{GROUP_LABEL[g]}</option>
          ))}
        </select>
      </div>

      {v.kind === 'http' ? (
        <>
          <div className="sm:col-span-2 lg:col-span-3">
            <label className={label} htmlFor="t-url">URL</label>
            <input id="t-url" className={input} required placeholder="https://example.com/" value={v.url ?? ''} onChange={(e) => set('url', e.target.value)} />
          </div>
          <div>
            <label className={label} htmlFor="t-status">Expected status (optional)</label>
            <input id="t-status" className={input} type="number" min={100} max={599} value={v.expect_status ?? ''} onChange={(e) => set('expect_status', numOrNull(e.target.value))} />
          </div>
        </>
      ) : (
        <div className="sm:col-span-2">
          <label className={label} htmlFor="t-host">{v.kind === 'dns' ? 'Resolver IP' : 'Host or IP'}</label>
          <input id="t-host" className={input} required placeholder={v.kind === 'dns' ? '192.168.2.1' : '192.168.2.10'} value={v.host ?? ''} onChange={(e) => set('host', e.target.value)} />
        </div>
      )}

      {v.kind === 'tcp' && (
        <div>
          <label className={label} htmlFor="t-port">Port</label>
          <input id="t-port" className={input} type="number" required min={1} max={65535} value={v.port ?? ''} onChange={(e) => set('port', numOrNull(e.target.value))} />
        </div>
      )}

      {v.kind === 'dns' && (
        <>
          <div>
            <label className={label} htmlFor="t-query">Query name</label>
            <input id="t-query" className={input} placeholder="www.google.com" value={v.dns_query ?? ''} onChange={(e) => set('dns_query', e.target.value || null)} />
          </div>
          <div>
            <label className={label} htmlFor="t-rdtype">Record</label>
            <select id="t-rdtype" className={input} value={v.dns_rdtype ?? 'A'} onChange={(e) => set('dns_rdtype', e.target.value)}>
              <option>A</option>
              <option>AAAA</option>
            </select>
          </div>
        </>
      )}

      <div>
        <label className={label} htmlFor="t-interval">Interval (s)</label>
        <input id="t-interval" className={input} type="number" required min={v.kind === 'icmp' ? 1 : 5} max={3600} step="any" value={v.interval_s} onChange={(e) => set('interval_s', Number(e.target.value))} />
      </div>
      <div>
        <label className={label} htmlFor="t-timeout">Timeout (s, optional)</label>
        <input id="t-timeout" className={input} type="number" min={0.2} max={60} step="any" value={v.timeout_s ?? ''} onChange={(e) => set('timeout_s', numOrNull(e.target.value))} />
      </div>
      <div className="flex items-end">
        <label className="inline-flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={v.enabled} onChange={(e) => set('enabled', e.target.checked)} />
          Enabled
        </label>
      </div>

      <p className="text-xs text-slate-500 sm:col-span-2 lg:col-span-4">{KIND_HELP[v.kind]}</p>
      {error && (
        <p role="alert" className="rounded-md bg-rose-950/60 px-3 py-2 text-sm text-rose-300 sm:col-span-2 lg:col-span-4">
          {error}
        </p>
      )}
      <div className="flex gap-2 sm:col-span-2 lg:col-span-4">
        <button type="submit" disabled={busy} className="rounded-md bg-sky-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50">
          {busy ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="rounded-md px-4 py-1.5 text-sm text-slate-400 hover:text-white">
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
