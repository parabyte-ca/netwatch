import type { Cause } from '../lib/api';

function bar(confidence: number) {
  if (confidence >= 80) return 'bg-rose-500';
  if (confidence >= 60) return 'bg-amber-400';
  return 'bg-slate-500';
}

/** Ranked likely causes with their confidence, evidence and next steps. */
export function CauseList({ causes, limit = 3 }: { causes: Cause[]; limit?: number }) {
  return (
    <ol className="space-y-3">
      {causes.slice(0, limit).map((c, i) => (
        <li key={c.id} className={i === 0 ? '' : 'opacity-80'}>
          <div className="flex items-center gap-3">
            <span className="w-10 shrink-0 text-right text-xs tabular-nums text-slate-400">{c.confidence} %</span>
            <div className="h-1.5 w-20 shrink-0 rounded bg-slate-800" aria-hidden>
              <div className={`h-1.5 rounded ${bar(c.confidence)}`} style={{ width: `${c.confidence}%` }} />
            </div>
            <span className={`text-sm ${i === 0 ? 'font-semibold text-white' : 'text-slate-200'}`}>{c.title}</span>
          </div>
          <div className="ml-[8.5rem] mt-1 space-y-1 text-xs">
            {c.evidence.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-4 text-slate-400">
                {c.evidence.map((e) => <li key={e}>{e}</li>)}
              </ul>
            )}
            {i === 0 && c.next_steps.length > 0 && (
              <div>
                <p className="mt-1 font-medium text-slate-300">Next steps</p>
                <ul className="list-disc space-y-0.5 pl-4 text-slate-300">
                  {c.next_steps.map((s) => <li key={s}>{s}</li>)}
                </ul>
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
