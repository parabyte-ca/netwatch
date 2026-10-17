import { fmtTime } from '../lib/format';

type Json = string | number | boolean | null | Json[] | { [k: string]: Json };

const label = (k: string) => k.replace(/_ms$/, '').replace(/_/g, ' ');

function Value({ k, v }: { k: string; v: Json }) {
  if (v === null || v === '') return <span className="text-slate-500">—</span>;
  if (typeof v === 'number' && k.endsWith('_ms')) return <>{fmtTime(v)}</>;
  if (Array.isArray(v)) return <>{v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ')}</>;
  if (typeof v === 'object') {
    return <>{Object.entries(v).map(([a, b]) => `${a}: ${Array.isArray(b) ? b.join(', ') : String(b ?? '—')}`).join(' · ')}</>;
  }
  return <>{String(v)}</>;
}

/** Event evidence: lists of records as tables, everything else as labelled values. */
export function Evidence({ items }: { items: [string, unknown][] }) {
  return (
    <div className="space-y-3 text-xs">
      {items.map(([k, raw]) => {
        const v = raw as Json;
        const rows = Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === 'object' && !Array.isArray(x))
          ? (v as { [k: string]: Json }[])
          : null;
        if (rows) {
          const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
          return (
            <div key={k}>
              <h3 className="mb-1 text-slate-500">{label(k)}</h3>
              <div className="overflow-x-auto">
                <table className="w-full max-w-3xl">
                  <thead className="text-left text-slate-500">
                    <tr>{cols.map((c) => <th key={c} className="pr-4 font-normal">{label(c)}</th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className="border-t border-slate-800 text-slate-200">
                        {cols.map((c) => <td key={c} className="py-0.5 pr-4 font-mono"><Value k={c} v={r[c] ?? null} /></td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        }
        return (
          <div key={k}>
            <span className="text-slate-500">{label(k)}: </span>
            <span className="font-mono text-slate-200"><Value k={k} v={v} /></span>
          </div>
        );
      })}
    </div>
  );
}
