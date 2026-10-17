import type { PathTest } from '../lib/api';
import { fmtMs, fmtTime } from '../lib/format';

/** mtr result. The first hop where loss persists to the destination is highlighted. */
export function HopTable({ test }: { test: PathTest }) {
  if (test.status === 'error') return <p className="text-sm text-rose-300">Path test failed: {test.error}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-xs">
        <thead className="text-slate-500">
          <tr>
            <th className="py-1 pr-3">#</th>
            <th className="pr-3">Host</th>
            <th className="pr-3 text-right">Loss</th>
            <th className="pr-3 text-right">Avg</th>
            <th className="pr-3 text-right">Best</th>
            <th className="pr-3 text-right">Worst</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {test.hops.map((h) => {
            const first = test.first_loss_hop === h.hop;
            const lossy = test.first_loss_hop !== null && h.hop >= test.first_loss_hop && h.host;
            return (
              <tr key={h.hop} className={first ? 'bg-rose-950/50' : undefined}>
                <td className="py-0.5 pr-3 text-slate-500">{h.hop}</td>
                <td className="pr-3 text-slate-200">{h.host ?? <span className="text-slate-600">no reply</span>}</td>
                <td className={`pr-3 text-right ${lossy ? 'text-rose-300' : h.loss_pct > 0 ? 'text-slate-500' : 'text-slate-300'}`}>
                  {h.host ? `${h.loss_pct.toFixed(0)}%` : '—'}
                </td>
                <td className="pr-3 text-right text-slate-300">{h.host ? fmtMs(h.avg) : '—'}</td>
                <td className="pr-3 text-right text-slate-400">{h.host ? fmtMs(h.best) : '—'}</td>
                <td className="pr-3 text-right text-slate-400">{h.host ? fmtMs(h.worst) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-slate-500">
        {fmtTime(test.ts_ms)} · {test.trigger} · {test.reached ? 'destination reached' : 'destination not reached'}
        {test.first_loss_hop !== null && <> · loss starts at hop {test.first_loss_hop}</>}
        {test.path_changed && <> · <span className="text-amber-300">route changed since the previous test</span></>}
      </p>
      {test.first_loss_hop === null && test.hops.some((h) => h.host && h.loss_pct > 0) && (
        <p className="text-[11px] text-slate-500">Loss shown only at intermediate hops is usually ICMP rate-limiting, not real loss.</p>
      )}
    </div>
  );
}
