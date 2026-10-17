import { useState, type FormEvent } from 'react';
import { HopTable } from '../components/HopTable';
import { api, type IperfServer, type IperfServerInput } from '../lib/api';
import { fmtBps, fmtTime } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const input = 'rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label={title}>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
      {children}
    </section>
  );
}

function NicSection() {
  const { data, error } = usePolling(api.nic, 5_000);
  if (error) return <p className="text-rose-400">{error}</p>;
  return (
    <Section title="TrueNAS network interfaces">
      {!data ? <p className="text-sm text-slate-500">Loading…</p> : data.interfaces.length === 0 ? (
        <p className="text-sm text-slate-500">No interface detected (set NETWATCH_NIC_INTERFACES if needed).</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {data.interfaces.map((n) => (
            <div key={n.iface} className="rounded-md border border-slate-800 p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-mono text-white">{n.iface}</span>
                <span className={n.operstate === 'up' ? 'text-emerald-300' : 'text-rose-300'}>{n.present ? n.operstate : 'missing'}</span>
              </div>
              {n.present && (
                <dl className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
                  <dt className="text-slate-500">Speed</dt><dd className="text-slate-200">{n.speed ? `${n.speed} Mb/s ${n.duplex ?? ''}` : '—'}</dd>
                  <dt className="text-slate-500">Link changes</dt><dd className="text-slate-200">{n.carrier_changes ?? '—'}</dd>
                  <dt className="text-slate-500">RX / TX errors</dt><dd className="text-slate-200">{n.counters?.rx_errors ?? 0} / {n.counters?.tx_errors ?? 0}</dd>
                  <dt className="text-slate-500">CRC errors</dt><dd className="text-slate-200">{n.counters?.rx_crc_errors ?? 0}</dd>
                  <dt className="text-slate-500">RX / TX drops</dt><dd className="text-slate-200">{n.counters?.rx_dropped ?? 0} / {n.counters?.tx_dropped ?? 0}</dd>
                </dl>
              )}
            </div>
          ))}
        </div>
      )}
      <p className="mt-2 text-[11px] text-slate-500">Counters are since the interface came up. Link drops, flaps, speed changes and errors appear on the Timeline.</p>
    </Section>
  );
}

function PathSection() {
  const targets = usePolling(api.targets, 60_000);
  const tests = usePolling(() => api.pathTests(), 30_000);
  const [choice, setChoice] = useState('');
  const [host, setHost] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  async function run(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const t = await api.runPathTest(choice ? { target_id: Number(choice) } : { host });
      setOpen(t.id);
      await tests.refresh();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setBusy(false);
    }
  }

  const routable = (targets.data ?? []).filter((t) => t.kind !== 'dns');
  return (
    <Section title="Path tests (mtr)">
      <form onSubmit={run} className="mb-3 flex flex-wrap items-end gap-2" aria-label="Run path test">
        <select aria-label="Target" className={input} value={choice} onChange={(e) => setChoice(e.target.value)}>
          <option value="">Custom host…</option>
          {routable.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.host})</option>)}
        </select>
        {!choice && <input aria-label="Host" className={input} placeholder="1.1.1.1" value={host} onChange={(e) => setHost(e.target.value)} required />}
        <button type="submit" disabled={busy} className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>{busy ? 'Tracing…' : 'Run path test'}</button>
        {err && <span role="alert" className="text-sm text-rose-300">{err}</span>}
      </form>
      <div className="divide-y divide-slate-800">
        {(tests.data ?? []).slice(0, 15).map((t) => (
          <div key={t.id} className="py-1.5">
            <button onClick={() => setOpen(open === t.id ? null : t.id)} className="flex w-full items-center gap-3 text-left text-sm">
              <span className="w-40 shrink-0 text-xs text-slate-500">{fmtTime(t.ts_ms)}</span>
              <span className="font-mono text-slate-200">{t.host}</span>
              <span className="text-xs text-slate-500">{t.trigger}</span>
              <span className={`ml-auto text-xs ${t.status === 'error' ? 'text-rose-300' : t.first_loss_hop ? 'text-amber-300' : 'text-emerald-300'}`}>
                {t.status === 'error' ? 'error' : t.first_loss_hop ? `loss from hop ${t.first_loss_hop}` : `${t.hop_count} hops, clean`}
              </span>
            </button>
            {open === t.id && <div className="mt-2"><HopTable test={t} /></div>}
          </div>
        ))}
        {tests.data?.length === 0 && <p className="text-sm text-slate-500">No path tests yet. Public targets are traced every 15 minutes and whenever an outage starts.</p>}
      </div>
    </Section>
  );
}

const EMPTY_SERVER: IperfServerInput = { name: '', host: '', port: 5201, kind: 'lan', duration_s: 10, parallel: 1, interval_min: 0, enabled: true };

function ThroughputSection() {
  const servers = usePolling(api.iperfServers, 60_000);
  const results = usePolling(api.iperfTests, 30_000);
  const [form, setForm] = useState<IperfServerInput | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [running, setRunning] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setErr(null);
    try {
      await fn();
      await Promise.all([servers.refresh(), results.refresh()]);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    }
  }
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    void act(async () => {
      if (editing) await api.updateIperfServer(editing, form);
      else await api.createIperfServer(form);
      setForm(null);
      setEditing(null);
    });
  };
  const run = (s: IperfServer) => {
    setRunning(s.id);
    void act(() => api.runIperf(s.id)).finally(() => setRunning(null));
  };
  const set = <K extends keyof IperfServerInput>(k: K, v: IperfServerInput[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));
  const name = (id: number | null) => servers.data?.find((s) => s.id === id)?.name ?? 'deleted server';

  return (
    <Section title="Throughput (iperf3)">
      <p className="mb-3 text-xs text-slate-500">
        LAN: run <code className="rounded bg-slate-800 px-1">iperf3 -s</code> on a PC or phone app and add it here. WAN: add a public iperf3 server
        (traffic leaves your network only when you run it). Comparing the two separates LAN problems from Bell.
      </p>
      {err && <p role="alert" className="mb-2 text-sm text-rose-300">{err}</p>}
      <div className="mb-3 space-y-1">
        {(servers.data ?? []).map((s) => (
          <div key={s.id} className="flex flex-wrap items-center gap-3 rounded-md border border-slate-800 px-3 py-2 text-sm">
            <span className="text-white">{s.name}</span>
            <span className="font-mono text-xs text-slate-400">{s.host}:{s.port}</span>
            <span className="rounded bg-slate-800 px-1.5 text-[11px] uppercase text-slate-300">{s.kind}</span>
            <span className="text-xs text-slate-500">{s.interval_min ? `every ${s.interval_min} min` : 'manual'}</span>
            <span className="ml-auto flex gap-2">
              <button disabled={running !== null} onClick={() => run(s)} className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>
                {running === s.id ? 'Testing…' : 'Run'}
              </button>
              <button onClick={() => { setEditing(s.id); setForm({ ...s }); }} className="px-2 text-slate-400 hover:text-white">Edit</button>
              <button onClick={() => { if (window.confirm(`Remove ${s.name}? Results are kept.`)) void act(() => api.deleteIperfServer(s.id)); }} className="px-2 text-rose-400 hover:text-rose-300">Delete</button>
            </span>
          </div>
        ))}
      </div>
      {form ? (
        <form onSubmit={save} className="mb-4 flex flex-wrap items-end gap-2" aria-label="iperf3 server">
          <input aria-label="Name" className={input} placeholder="Name" value={form.name} onChange={(e) => set('name', e.target.value)} required />
          <input aria-label="Server host" className={input} placeholder="192.168.2.2" value={form.host} onChange={(e) => set('host', e.target.value)} required />
          <input aria-label="Port" className={`${input} w-24`} type="number" value={form.port} onChange={(e) => set('port', Number(e.target.value))} />
          <select aria-label="Kind" className={input} value={form.kind} onChange={(e) => set('kind', e.target.value as 'lan' | 'wan')}>
            <option value="lan">LAN</option><option value="wan">WAN</option>
          </select>
          <input aria-label="Duration (s)" className={`${input} w-24`} type="number" min={3} max={60} value={form.duration_s} onChange={(e) => set('duration_s', Number(e.target.value))} />
          <input aria-label="Schedule (min, 0 = manual)" className={`${input} w-28`} type="number" min={0} value={form.interval_min} onChange={(e) => set('interval_min', Number(e.target.value))} />
          <button type="submit" className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>Save</button>
          <button type="button" onClick={() => { setForm(null); setEditing(null); }} className="px-2 text-sm text-slate-400">Cancel</button>
        </form>
      ) : (
        <button onClick={() => setForm(EMPTY_SERVER)} className={`${btn} mb-4 bg-slate-800 text-slate-200 hover:bg-slate-700`}>Add iperf3 server</button>
      )}
      {(results.data ?? []).length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500"><tr><th className="py-1">When</th><th>Server</th><th>Direction</th><th className="text-right">Throughput</th><th className="text-right">Retransmits</th></tr></thead>
          <tbody className="divide-y divide-slate-800">
            {(results.data ?? []).slice(0, 20).map((r) => (
              <tr key={r.id}>
                <td className="py-1 text-xs text-slate-500">{fmtTime(r.ts_ms)}</td>
                <td className="text-slate-300">{name(r.server_id)} <span className="text-[11px] uppercase text-slate-500">{r.kind}</span></td>
                <td className="text-slate-400">{r.direction}</td>
                <td className={`text-right font-mono ${r.status === 'ok' ? 'text-slate-100' : 'text-rose-300'}`}>{r.status === 'ok' ? fmtBps(r.bits_per_second) : r.error}</td>
                <td className="text-right text-slate-400">{r.retransmits ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

export function Diagnostics() {
  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-white">Diagnostics</h1>
      <NicSection />
      <PathSection />
      <ThroughputSection />
    </div>
  );
}
