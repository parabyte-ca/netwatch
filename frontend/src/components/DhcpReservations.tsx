import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type AdGuardDhcp, type DhcpLease } from '../lib/api';
import { usePolling } from '../lib/usePolling';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';
const input = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const code = (s: string) => <code className="rounded bg-slate-800 px-1 font-mono text-xs">{s}</code>;
const RENEW = 'The device gets a new address when it renews its lease or reconnects to the network.';

/** A hostname AdGuard Home accepts, from a device name ("Sam's Pixel" → "sam-s-pixel"). */
export function hostnameFor(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/-{2,}/g, '-').replace(/^-|-$/g, '').slice(0, 63);
}

/** AdGuard Home's DHCP leases, when actions are on and AdGuard Home is the DHCP server; else null. */
export function useDhcp() {
  const q = usePolling(api.adguardDhcp, 60_000);
  const ready = !!q.data && q.data.available && q.data.enabled && !!q.data.v4;
  return { dhcp: ready ? (q.data as AdGuardDhcp) : null, refresh: q.refresh };
}

function reserveConfirm(label: string, mac: string, ip: string, hostname: string, from: string | null, run: () => Promise<void>): Confirm {
  return {
    title: from ? `Change ${label}'s reservation to ${ip}?` : `Reserve ${ip} for ${label}?`,
    body: (
      <>
        <p>
          {from ? <>Moves the reservation for {code(mac)} from {code(from)} to {code(ip)}</> : <>AdGuard Home will always give {code(ip)} to {code(mac)}</>}
          {hostname ? <> with the name {code(hostname)}</> : null}.
        </p>
        <p className="text-xs text-slate-400">{RENEW} The change is written to the security log.</p>
      </>
    ),
    confirmLabel: from ? 'Change' : 'Reserve',
    run,
  };
}

function removeConfirm(r: DhcpLease, run: () => Promise<void>): Confirm {
  const who = r.device?.label ?? r.hostname ?? r.mac;
  return {
    title: `Remove the reservation of ${r.ip} for ${who}?`,
    body: (
      <>
        <p>AdGuard Home will no longer keep {code(r.ip)} for {code(r.mac)}; the device gets an address from the DHCP range next time.</p>
        {!r.by_netwatch && <p className="text-xs text-amber-300">This reservation was made in AdGuard Home, not NetWatch.</p>}
      </>
    ),
    confirmLabel: 'Remove reservation',
    danger: true,
    run,
  };
}

/** Device page: the device's reservation, or reserve its current address. */
export function DeviceReservation({ d }: { d: { id: number; label: string; macs: string[]; mac: string; last_ip: string | null; randomised: boolean } }) {
  const { dhcp, refresh } = useDhcp();
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [edit, setEdit] = useState(false);
  const [ip, setIp] = useState('');
  const [name, setName] = useState('');
  const [note, setNote] = useState<string | null>(null);
  if (!dhcp) return null;
  const mine = dhcp.reservations.find((r) => d.macs.includes(r.mac));
  const lease = dhcp.leases.find((l) => d.macs.includes(l.mac) && l.ip === d.last_ip) ?? dhcp.leases.find((l) => d.macs.includes(l.mac));
  const mac = mine?.mac ?? lease?.mac ?? d.mac;
  const open = () => {
    setIp(mine?.ip ?? lease?.ip ?? d.last_ip ?? '');
    setName(mine?.hostname ?? hostnameFor(d.label));
    setEdit(true);
  };
  const save = () =>
    setAsk(reserveConfirm(d.label, mac, ip.trim(), hostnameFor(name), mine && mine.ip !== ip.trim() ? mine.ip : null, async () => {
      const r = await api.adguardReserve(mac, ip.trim(), hostnameFor(name));
      setEdit(false);
      setNote(r.reservation.changed_from ? `Changed from ${r.reservation.changed_from} to ${r.reservation.ip}. ${RENEW}` : `Reserved ${r.reservation.ip}.`);
      await refresh();
    }));
  return (
    <section className="space-y-2 rounded-lg bg-slate-900 p-4 text-sm ring-1 ring-slate-800" aria-label="DHCP reservation">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">DHCP reservation</h2>
      {mine ? (
        <p className="text-slate-200">
          AdGuard Home always gives this device {code(mine.ip)}{mine.hostname && <> as {code(mine.hostname)}</>}.
          {!mine.by_netwatch && <span className="text-xs text-slate-500"> (made in AdGuard Home)</span>}
        </p>
      ) : (
        <p className="text-slate-400">No reservation: the address comes from AdGuard Home's DHCP range{lease ? <> (now {code(lease.ip)})</> : null}.</p>
      )}
      {(d.randomised || (parseInt(mac[1] ?? '0', 16) & 2) === 2) && (
        <p className="text-xs text-amber-300">This device uses a private (randomised) Wi-Fi address: a reservation only holds while it keeps this address. Turn off "private address" for your network on the device to make it stick.</p>
      )}
      {edit ? (
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-slate-400">IP address
            <input aria-label="Reserved IP address" className={`${input} w-36 font-mono`} value={ip} onChange={(e) => setIp(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">Name
            <input aria-label="Reservation name" className={`${input} w-44`} maxLength={63} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!ip.trim()} onClick={save}>{mine ? 'Save' : 'Reserve'}</button>
          <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={() => setEdit(false)}>Cancel</button>
          <p className="w-full text-xs text-slate-500">
            Network {dhcp.v4!.gateway_ip}/{dhcp.v4!.subnet_mask}, DHCP range {dhcp.v4!.range_start} to {dhcp.v4!.range_end}. {RENEW}
          </p>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} onClick={open}>{mine ? 'Change' : 'Reserve this address'}</button>
          {mine && <button className={`${btn} text-rose-300 ring-1 ring-slate-700 hover:bg-slate-800`}
            onClick={() => setAsk(removeConfirm({ ...mine, device: { id: d.id, label: d.label } }, async () => { await api.adguardUnreserve(mine.mac); setNote('Reservation removed.'); await refresh(); }))}>Remove</button>}
        </div>
      )}
      {note && <p className="text-xs text-emerald-300">{note}</p>}
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </section>
  );
}

/** DNS page: every reservation in AdGuard Home, with change and remove. */
export function ReservationList() {
  const { dhcp, refresh } = useDhcp();
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [ip, setIp] = useState('');
  if (!dhcp) return null;
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="DHCP reservations">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">DHCP reservations</h2>
      {dhcp.reservations.length === 0 ? (
        <p className="text-sm text-slate-400">No reservations yet. Reserve an address from a device's page, or tick devices on the Devices page and choose Reserve current addresses.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr><th className="py-1 pr-3">Address</th><th className="pr-3">Device</th><th className="pr-3">MAC</th><th className="pr-3">Name</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {dhcp.reservations.map((r) => (
                <tr key={r.mac}>
                  <td className="py-1.5 pr-3 font-mono">
                    {editing === r.mac ? (
                      <input aria-label={`New address for ${r.mac}`} className={`${input} w-32 font-mono`} value={ip} onChange={(e) => setIp(e.target.value)} />
                    ) : r.ip}
                  </td>
                  <td className="pr-3">
                    {r.device ? <Link to={`/devices/${r.device.id}`} className="text-sky-400 hover:underline">{r.device.label}</Link> : <span className="text-slate-500">unknown</span>}
                    {r.by_netwatch && <span className="ml-2 rounded bg-slate-800 px-1.5 text-[10px] text-slate-400">added by NetWatch</span>}
                  </td>
                  <td className="pr-3 font-mono text-xs text-slate-400">{r.mac}</td>
                  <td className="pr-3 text-slate-300">{r.hostname ?? ''}</td>
                  <td className="whitespace-nowrap text-right">
                    {editing === r.mac ? (
                      <>
                        <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={!ip.trim() || ip.trim() === r.ip}
                          onClick={() => setAsk(reserveConfirm(r.device?.label ?? r.hostname ?? r.mac, r.mac, ip.trim(), r.hostname ?? '', r.ip, async () => {
                            await api.adguardReserve(r.mac, ip.trim(), r.hostname ?? undefined); setEditing(null); await refresh();
                          }))}>Save</button>
                        <button className={`${btn} ml-1 text-slate-300`} onClick={() => setEditing(null)}>Cancel</button>
                      </>
                    ) : (
                      <>
                        <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => { setEditing(r.mac); setIp(r.ip); }}>Change</button>
                        <button className={`${btn} ml-1 text-rose-300 ring-1 ring-slate-700 hover:bg-slate-800`}
                          onClick={() => setAsk(removeConfirm(r, async () => { await api.adguardUnreserve(r.mac); await refresh(); }))}>Remove</button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </section>
  );
}

/** Devices page selection bar: reserve the ticked devices' current addresses. */
export function ReserveCurrentButton({ ids, onDone }: { ids: number[]; onDone: () => Promise<void> }) {
  const { dhcp, refresh } = useDhcp();
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [result, setResult] = useState<string | null>(null);
  if (!dhcp) return null;
  const n = ids.length;
  return (
    <>
      <button className="rounded-md bg-slate-700 px-3 py-1.5 text-sm font-medium text-slate-100 hover:bg-slate-600"
        onClick={() => setAsk({
          title: `Reserve the current address of ${n} device${n === 1 ? '' : 's'}?`,
          body: <p>Each device keeps the address AdGuard Home gave it now, under its current MAC. Devices without a current lease from AdGuard Home are skipped.</p>,
          confirmLabel: 'Reserve',
          run: async () => {
            const r = await api.adguardReserveCurrent(ids);
            const ok = r.results.filter((x) => x.ok && !x.already).length;
            const already = r.results.filter((x) => x.already).length;
            const failed = r.results.filter((x) => !x.ok);
            setResult([`Reserved ${ok}`, already ? `${already} already reserved` : '',
              ...failed.map((x) => `${x.label ?? x.id}: ${x.error}`)].filter(Boolean).join(' · '));
            await refresh();
            await onDone();
          },
        })}>Reserve current addresses</button>
      {result && <p className="w-full text-xs text-slate-300" role="status">{result}</p>}
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </>
  );
}
