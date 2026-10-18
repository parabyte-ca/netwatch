import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { DeviceTraffic } from '../components/DeviceTraffic';
import { EventRow } from '../components/EventRow';
import { api, type DeviceType } from '../lib/api';
import { DEVICE_TYPE_LABEL, fmtAgo, fmtTime } from '../lib/format';
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

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  if (v === null || v === undefined || v === '') return null;
  return (
    <>
      <dt className="text-slate-500">{k}</dt>
      <dd className="break-words text-slate-200">{v}</dd>
    </>
  );
}

export function DeviceDetail() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const { data, error, refresh } = usePolling(() => api.device(id), 15_000, [id]);
  const others = usePolling(() => api.devices(), 60_000);
  const [name, setName] = useState('');
  const [type, setType] = useState('');
  const [notes, setNotes] = useState('');
  const [mergeInto, setMergeInto] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const d = data?.device;
  useEffect(() => {
    if (!d) return;
    setName(d.name ?? '');
    setType(d.type_override ?? '');
    setNotes(d.notes ?? '');
  }, [d?.id, d?.name, d?.type_override, d?.notes]);
  useEffect(() => {
    // a merged member's URL shows its primary device
    if (d && d.id !== id) nav(`/devices/${d.id}`, { replace: true });
  }, [d?.id, id]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await refresh();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="text-rose-400">{error}</p>;
  if (!data || !d) return <p className="text-slate-500">Loading…</p>;

  const save = (e: FormEvent) => {
    e.preventDefault();
    void act(() => api.editDevice(d.id, { name: name.trim() || null, type_override: (type || null) as DeviceType | null, notes: notes.trim() || null }));
  };
  const det = d.details;
  const candidates = (others.data?.devices ?? []).filter((o) => o.id !== d.id);

  return (
    <div className="space-y-5">
      <div>
        <Link to="/devices" className="text-xs text-slate-500 hover:text-slate-300">← Devices</Link>
        <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold text-white">
          <span className={`h-2.5 w-2.5 rounded-full ${d.online ? 'bg-emerald-400' : 'bg-slate-600'}`} />
          {d.label}
        </h1>
        <p className="text-sm text-slate-400">
          {DEVICE_TYPE_LABEL[d.type] ?? d.type}
          {d.last_ip && <> · {d.last_ip}</>}
          {' · '}
          {d.online ? 'online' : `last seen ${fmtAgo(d.last_seen_ms, Date.now())}`}
          {' · first seen '}
          {fmtTime(d.first_seen_ms)}
        </p>
      </div>
      {err && <p className="text-sm text-rose-400">{err}</p>}

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Identity">
          <dl className="grid grid-cols-[9rem_1fr] gap-y-1 text-sm">
            <Row k="MAC" v={<span className="font-mono">{d.macs.join(', ')}</span>} />
            <Row k="Vendor" v={d.vendor ?? (d.randomised ? 'Private (randomised) address: no vendor' : 'Not in the IEEE registry')} />
            <Row k="DHCP hostname" v={d.hostname} />
            <Row k="mDNS name" v={d.mdns_name} />
            <Row k="Reverse DNS" v={det.ptr} />
            <Row k="Model" v={d.model} />
            <Row k="Deco app name" v={det.app_name} />
            <Row k="Connected to" v={det.deco_name ? `Deco ${det.deco_name}${det.connection ? ` (${det.connection})` : ''}` : null} />
            <Row k="Advertises" v={det.services?.join(', ')} />
            <Row k="UPnP" v={det.ssdp?.join(' · ')} />
            <Row k="DHCP client" v={det.dhcp_vendor} />
            <Row k="Type guess" v={`${DEVICE_TYPE_LABEL[d.type_auto] ?? d.type_auto} (${d.type_reason ?? 'no evidence'})`} />
            <Row k="Seen via" v={det.sources?.join(', ')} />
          </dl>
        </Section>

        <Section title="Label and monitoring">
          <form onSubmit={save} className="space-y-2" aria-label="Edit device">
            <div className="flex flex-wrap gap-2">
              <input aria-label="Name" placeholder={d.label} className={`${input} flex-1`} maxLength={64}
                value={name} onChange={(e) => setName(e.target.value)} />
              <select aria-label="Device type" className={input} value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">Type: automatic</option>
                {Object.entries(DEVICE_TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <textarea aria-label="Notes" placeholder="Notes (location, owner, …)" className={`${input} w-full`} rows={2}
              maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} />
            <button type="submit" disabled={busy} className={`${btn} bg-sky-600 text-white hover:bg-sky-500`}>Save</button>
          </form>
          <div className="mt-4 border-t border-slate-800 pt-3 text-sm">
            {data.target ? (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-slate-300">
                  Monitored as <Link to={`/targets/${data.target.id}`} className="text-sky-400 hover:underline">{data.target.name}</Link>
                  {' '}({data.target.host}, every {data.target.interval_s} s). The target follows the device if its address changes.
                </span>
                <button disabled={busy} className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}
                  onClick={() => { if (confirm('Stop monitoring and delete its ping history?')) void act(() => api.unmonitorDevice(d.id)); }}>
                  Stop monitoring
                </button>
              </div>
            ) : (
              <div className="space-y-1">
                <button disabled={busy || !d.last_ip} className={`${btn} bg-emerald-700 text-white hover:bg-emerald-600`}
                  onClick={() => void act(() => api.monitorDevice(d.id))}>
                  Monitor this device
                </button>
                <p className="text-xs text-slate-500">
                  Adds a ping target (every 5 s) in the Devices group. Phones and tablets sleep and stop answering pings, so
                  this works best for wired and always-on devices: Decos, printers, TVs, PCs.
                </p>
              </div>
            )}
          </div>
        </Section>
      </div>

      <Section title="Bandwidth">
        <DeviceTraffic id={d.id} />
      </Section>

      <Section title="IP address history">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr><th className="py-1">Address</th><th>First seen</th><th>Last seen</th><th>Learnt from</th></tr>
            </thead>
            <tbody>
              {data.ips.map((ip) => (
                <tr key={`${ip.device_id}-${ip.ip}`} className="border-t border-slate-800">
                  <td className="py-1 font-mono text-xs text-slate-200">{ip.ip}</td>
                  <td className="text-xs text-slate-400">{fmtTime(ip.first_seen_ms)}</td>
                  <td className="text-xs text-slate-400">{fmtTime(ip.last_seen_ms)}</td>
                  <td className="text-xs text-slate-500">{ip.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.ips.length === 0 && <p className="text-sm text-slate-500">No address seen yet.</p>}
      </Section>

      <Section title="Addresses of this device">
        <p className="mb-2 text-xs text-slate-500">
          Phones and laptops use a private (randomised) MAC per network and sometimes rotate it. NetWatch links them
          automatically when they report the same hostname; you can also merge them by hand.
        </p>
        <ul className="space-y-1 text-sm">
          {data.members.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-xs text-slate-300">{m.mac}</span>
              <span className="text-xs text-slate-500">{m.last_ip ?? '—'} · last seen {fmtAgo(m.last_seen_ms, Date.now())}</span>
              {m.merged_into && (
                <button disabled={busy} className="text-xs text-sky-400 hover:underline" onClick={() => void act(() => api.unmergeDevice(m.id))}>
                  Split off
                </button>
              )}
            </li>
          ))}
        </ul>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select aria-label="Merge into" className={input} value={mergeInto} onChange={(e) => setMergeInto(e.target.value)}>
            <option value="">Same device as…</option>
            {candidates.map((o) => <option key={o.id} value={o.id}>{o.label} ({o.last_ip ?? o.macs[0]})</option>)}
          </select>
          <button disabled={busy || !mergeInto} className={`${btn} bg-slate-800 text-slate-200 hover:bg-slate-700`}
            onClick={() => void act(async () => { const r = await api.mergeDevice(d.id, Number(mergeInto)); setMergeInto(''); nav(`/devices/${r.device.id}`); })}>
            Merge
          </button>
        </div>
      </Section>

      <Section title="Events involving this device (30 days)">
        {data.events.length ? data.events.map((e) => <EventRow key={e.id} ev={e} compact />) : (
          <p className="text-sm text-slate-500">No anomalies or changes recorded for this device.</p>
        )}
      </Section>

      <div className="flex justify-end">
        <button disabled={busy || !!data.target} title={data.target ? 'Stop monitoring first' : undefined}
          className="text-xs text-rose-400 hover:underline disabled:opacity-50"
          onClick={() => { if (confirm('Forget this device? It reappears if it is seen again.')) void act(async () => { await api.forgetDevice(d.id); nav('/devices'); }); }}>
          Forget device
        </button>
      </div>
    </div>
  );
}
