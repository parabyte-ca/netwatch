import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type ExposureFinding, type OpenService, type PortForward } from '../lib/api';
import { fmtTime, SEVERITY_DOT } from '../lib/format';
import { usePolling } from '../lib/usePolling';

const btn = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50';

const RISK_STYLE: Record<OpenService['risk'], string> = {
  high: 'bg-rose-950 text-rose-300 ring-rose-800',
  medium: 'bg-amber-950 text-amber-300 ring-amber-800',
  info: 'bg-slate-800 text-slate-300 ring-slate-700',
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label={title}>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
      {children}
    </section>
  );
}

function Finding({ f }: { f: ExposureFinding }) {
  return (
    <li className="flex gap-3 py-2">
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[f.severity]}`} aria-hidden />
      <div className="min-w-0 text-sm">
        <p className="text-slate-100">
          {f.device_id ? <Link to={`/devices/${f.device_id}`} className="hover:text-sky-300">{f.title}</Link> : f.title}
        </p>
        <p className="text-xs text-slate-400">{f.advice}</p>
      </div>
    </li>
  );
}

function Forwards({ forwards }: { forwards: PortForward[] }) {
  if (!forwards.length) return <p className="text-sm text-slate-400">The Home Hub forwards no ports. Nothing on the LAN is reachable from the internet through it.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
          <tr><th className="py-1 pr-3">Internet port</th><th className="pr-3">To</th><th className="pr-3">Added by</th><th>Description</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {forwards.map((f, i) => (
            <tr key={i} className={f.enabled ? '' : 'text-slate-500'}>
              <td className="py-1.5 pr-3 tabular-nums">
                {f.external_port}{f.external_port_end ? `–${f.external_port_end}` : ''}/{f.protocol.toLowerCase()}
                {!f.enabled && ' (off)'}
              </td>
              <td className="pr-3">
                {f.device_id ? <Link to={`/devices/${f.device_id}`} className="text-slate-100 hover:text-sky-300">{f.device_label}</Link> : <span className="font-mono text-xs">{f.internal_ip}</span>}
                <span className="text-xs text-slate-500"> :{f.internal_port}</span>
              </td>
              <td className="pr-3">{f.origin === 'upnp' ? <span className="text-amber-300">the device (UPnP)</span> : 'you (Hub settings)'}</td>
              <td className="text-xs text-slate-400">{f.description ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Services({ services }: { services: OpenService[] }) {
  if (!services.length) return <p className="text-sm text-slate-400">No open services found on the checked ports.</p>;
  const byDevice = new Map<number, OpenService[]>();
  for (const s of services) byDevice.set(s.device_id, [...(byDevice.get(s.device_id) ?? []), s]);
  return (
    <ul className="divide-y divide-slate-800">
      {[...byDevice.entries()].map(([id, list]) => (
        <li key={id} className="py-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <Link to={`/devices/${id}`} className="text-sm text-slate-100 hover:text-sky-300">{list[0]!.device_label}</Link>
            <span className="font-mono text-xs text-slate-500">{list[0]!.ip}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {list.map((s) => (
              <span key={s.port} title={[s.advice, s.banner].filter(Boolean).join(' — ')} className={`rounded px-1.5 py-0.5 text-xs ring-1 ${RISK_STYLE[s.risk]}`}>
                {s.service} <span className="opacity-70">{s.port}</span>
              </span>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** What the network exposes: open services on LAN devices and the Home Hub's port forwards. */
export function Exposure() {
  const { data, error, refresh } = usePolling(api.exposure, 3_000);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setErr(null);
    try {
      await api.runExposure();
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  const p = data?.progress;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white">Exposure</h1>
          <p className="text-xs text-slate-400">
            {data?.latest ? `Last scan ${fmtTime(data.latest.ts_ms)}: ${data.latest.hosts} devices, ${data.latest.open_ports} open ports.` : 'No scan yet.'}{' '}
            {data && (data.nightly ? 'Runs nightly after the update window.' : 'Nightly scans are off (Admin → settings).')}
          </p>
        </div>
        <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!data || data.running} onClick={() => void run()}>
          {data?.running ? `Scanning… ${p && p.total ? Math.round((100 * p.done) / p.total) : 0}%` : 'Scan now'}
        </button>
      </div>
      {(err || error) && <p className="text-sm text-rose-400">{err || error}</p>}

      {data?.latest && (
        <Section title="Findings">
          {data.findings.length ? (
            <ul className="divide-y divide-slate-800">{data.findings.map((f, i) => <Finding key={i} f={f} />)}</ul>
          ) : (
            <p className="text-sm text-emerald-300">Nothing to worry about: no risky services and nothing forwarded from the internet.</p>
          )}
        </Section>
      )}

      <Section title="Reachable from the internet">
        {!data ? <p className="text-sm text-slate-500">Loading…</p> : !data.hub_configured ? (
          <p className="text-sm text-slate-400">
            Connect the Home Hub under <Link to="/admin" className="text-sky-400 hover:underline">Admin → Home Hub</Link> to see its port forwards, UPnP mappings and DMZ.
          </p>
        ) : !data.latest ? (
          <p className="text-sm text-slate-400">Run a scan to read the Hub's port forwards.</p>
        ) : !data.hub_read ? (
          <p className="text-sm text-amber-300">The Home Hub could not be read during the last scan (check Admin → Home Hub).</p>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-slate-400">
              UPnP is {data.hub?.upnp_enabled == null ? 'unknown' : data.hub.upnp_enabled ? 'on (devices can open ports themselves)' : 'off'}
              {data.hub?.dmz_known && <> · DMZ {data.hub.dmz_host ? <span className="text-rose-300">on → {data.hub.dmz_host}</span> : 'off'}</>}
            </p>
            <Forwards forwards={data.forwards} />
          </div>
        )}
      </Section>

      <Section title="Open services on the LAN">
        <p className="mb-2 text-xs text-slate-400">
          Checks about 30 well-known ports on every device seen in the last hour with a plain TCP connection. Red: should not run without a good reason; amber: fine on the LAN, risky if forwarded.
        </p>
        {data && <Services services={data.services} />}
      </Section>
    </div>
  );
}
