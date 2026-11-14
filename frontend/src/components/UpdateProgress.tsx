import { useEffect, useRef, useState } from 'react';
import type { UpdateProgressInfo, UpdateStage } from '../lib/api';
import {
  SLOW_MS, UPDATE_STAGES, isActive, knownEstimate, mmss, parseTs, progressFraction, readPending,
  rememberEstimate, startedAt,
} from '../lib/updateProgress';

/** Stages with what the page knows on top of the server's view: while NetWatch does not answer
 * after the agent started, it is restarting. */
export function effectiveStages(p: UpdateProgressInfo, offline: boolean): UpdateStage[] {
  const stages = p.stages?.length ? p.stages : UPDATE_STAGES;
  if (!offline) return stages;
  const started = stages.findIndex((s) => s.key === 'started');
  const restart = stages.findIndex((s) => s.key === 'restart' || s.key === 'health');
  if (started < 0 || restart < 0 || !stages[started]!.reached) return stages;
  return stages.map((s, i) => (i <= restart ? { ...s, reached: true } : s));
}

function Step({ s, i, n, current, failed, took }: { s: UpdateStage; i: number; n: number; current: number; failed: boolean; took: string | null }) {
  const done = i < current || (i === current && s.key === 'done');
  const active = i === current && !done;
  const red = failed && active;
  const dot = red
    ? 'bg-rose-500 ring-4 ring-rose-500/25'
    : done
      ? 'bg-sky-400'
      : active
        ? 'bg-sky-400 ring-4 ring-sky-400/25 motion-safe:animate-pulse'
        : 'bg-slate-700';
  return (
    <li className="flex min-w-0 flex-col items-center gap-1.5 text-center" aria-current={active ? 'step' : undefined}>
      <div className="flex w-full items-center">
        <span className={`h-0.5 flex-1 ${i === 0 ? 'invisible' : i <= current ? 'bg-sky-400' : 'bg-slate-700'}`} />
        <span className={`h-3 w-3 shrink-0 rounded-full transition-colors duration-500 ${dot}`} />
        <span className={`h-0.5 flex-1 ${i === n - 1 ? 'invisible' : i < current ? 'bg-sky-400' : 'bg-slate-700'}`} />
      </div>
      <span className={`hidden text-[11px] leading-tight sm:block ${red ? 'font-semibold text-rose-300' : active ? 'font-semibold text-white' : done ? 'text-slate-300' : 'text-slate-500'}`}>
        {s.label}
      </span>
      {took && <span className="hidden font-mono text-[10px] tabular-nums text-slate-500 sm:block">{took}</span>}
    </li>
  );
}

function Check() {
  return (
    <svg viewBox="0 0 24 24" className="h-10 w-10 shrink-0" aria-hidden>
      <circle cx="12" cy="12" r="11" className="fill-emerald-500/15 stroke-emerald-400" strokeWidth="1.5" />
      <path d="M7 12.5l3.2 3.2L17.5 8.5" fill="none" className="stroke-emerald-300 motion-safe:animate-draw" strokeWidth="2.2"
        strokeLinecap="round" strokeLinejoin="round" strokeDasharray="48" />
    </svg>
  );
}

function Cross() {
  return (
    <svg viewBox="0 0 24 24" className="h-10 w-10 shrink-0" aria-hidden>
      <circle cx="12" cy="12" r="11" className="fill-rose-500/15 stroke-rose-400" strokeWidth="1.5" />
      <path d="M8.5 8.5l7 7M15.5 8.5l-7 7" fill="none" className="stroke-rose-300 motion-safe:animate-draw" strokeWidth="2.2"
        strokeLinecap="round" strokeDasharray="48" />
    </svg>
  );
}

/** What an in-app update is doing: real stages from the update log, a bar that moves between
 * them on the usual run time, a timer, the latest log line, and a finish for success or failure. */
export function UpdateProgress({ progress, current, offline }: { progress: UpdateProgressInfo; current: string; offline: boolean }) {
  const active = isActive(progress.state);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  useEffect(() => rememberEstimate(progress.estimate_s), [progress.estimate_s]);

  const pending = readPending(now);
  const begin = startedAt(progress, pending) ?? now;
  const rollback = (progress.action ?? progress.request?.action ?? progress.status?.action) === 'rollback';
  const target = progress.request?.version ?? progress.status?.version ?? pending?.to ?? null;
  const stages = effectiveStages(progress, offline && active);
  let current_ = 0;
  stages.forEach((s, i) => {
    if (s.reached) current_ = i;
  });
  const succeeded = progress.state === 'succeeded';
  const failed = progress.state === 'failed';
  const estimate = knownEstimate(progress.estimate_s);
  const elapsed = (succeeded || failed ? (parseTs(progress.status?.finished_at) ?? now) : now) - begin;

  // Never let the bar go backwards (a late poll, or the server's view after a reconnect).
  const best = useRef(0);
  const frac = progressFraction(stages, elapsed / 1000, estimate, succeeded);
  if (frac > best.current || succeeded) best.current = frac;
  const pct = best.current;
  const leftMin = Math.ceil((estimate * 1000 - elapsed) / 60_000);

  if (succeeded) {
    const to = progress.status?.to_version ?? target ?? current;
    return (
      <div className="flex items-center gap-3" role="status">
        <Check />
        <div>
          <p className="font-medium text-emerald-200">{rollback ? `Rolled back to v${to}` : `Updated to v${to}`} in {mmss(elapsed)}</p>
          <p className="text-xs text-slate-400">Every step passed, including the health check.</p>
        </div>
      </div>
    );
  }

  const failedStage = stages.find((s) => s.key === progress.failed_at)?.label;
  return (
    <div className="min-w-0 space-y-4">
      {failed ? (
        <div className="flex items-center gap-3" role="alert">
          <Cross />
          <div className="min-w-0">
            <p className="font-medium text-rose-200">
              {rollback ? 'Rollback' : 'Update'} failed{failedStage ? ` at ${failedStage}` : ''}
              {progress.rolled_back && progress.status?.from_version ? `; rolled back to v${progress.status.from_version}` : ''}
            </p>
            <p className="text-xs text-slate-400">
              {progress.reason ?? progress.status?.error ?? (progress.rolled_back ? 'NetWatch is running the previous version again, with its data.' : 'See the log below.')}
            </p>
          </div>
        </div>
      ) : (
        <div className="flex items-baseline justify-between gap-3">
          <p className="font-medium text-white">{rollback ? 'Rolling back' : `Updating${target ? ` to v${target}` : ''}`}</p>
          <p className="font-mono text-sm tabular-nums text-slate-300" aria-label="Time elapsed">{mmss(elapsed)}</p>
        </div>
      )}

      <ol className="grid gap-1" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }} aria-label="Update steps">
        {stages.map((s, i) => {
          // How long a finished step took: until the next step's log line.
          const next = stages[i + 1];
          const took = s.at != null && next?.at != null && next.reached && i < current_ ? mmss((next.at - s.at) * 1000) : null;
          return <Step key={s.key} s={s} i={i} n={stages.length} current={current_} failed={failed} took={took} />;
        })}
      </ol>
      <p className="text-center text-xs text-slate-300 sm:hidden">{stages[current_]?.label}</p>

      {!failed && (
        <div className="space-y-1.5">
          <div className="relative h-3 overflow-hidden rounded-full bg-slate-800 ring-1 ring-slate-700" role="progressbar"
            aria-label="Update progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct * 100)}>
            <div className="h-full rounded-full bg-gradient-to-r from-sky-500 via-cyan-400 to-emerald-400 shadow-[0_0_14px_rgba(34,211,238,0.55)] transition-[width] duration-1000 ease-linear"
              style={{ width: `${Math.max(3, pct * 100)}%` }}>
              <div className="h-full w-full rounded-full opacity-30 motion-safe:animate-stripes"
                style={{ backgroundImage: 'repeating-linear-gradient(45deg, rgba(255,255,255,0.9) 0 7px, transparent 7px 14px)', backgroundSize: '28px 100%' }} />
            </div>
          </div>
          <div className="flex flex-wrap justify-between gap-x-3 text-xs text-slate-400">
            <span>
              {elapsed < estimate * 1000 ? `About ${leftMin} min left` : 'Taking a little longer than usual…'}
              {' · '}
              {progress.estimate_s ? `usually takes ${mmss(estimate * 1000)}` : 'first-time estimate'}
            </span>
            <span className="tabular-nums">{Math.round(pct * 100)}%</span>
          </div>
        </div>
      )}

      {offline && active ? (
        <p className="text-xs text-sky-200">NetWatch is restarting with the new version. This page reconnects by itself.</p>
      ) : progress.last_line && !failed ? (
        <p className="truncate font-mono text-xs text-slate-400" title={progress.last_line} aria-label="Latest log line">
          <span className="text-sky-400">›</span> {progress.last_line}
        </p>
      ) : null}
      {active && elapsed > SLOW_MS && (
        <p className="text-xs text-amber-300">
          This is taking longer than usual. On TrueNAS, check <code className="rounded bg-slate-800 px-1">sudo scripts/upgrade-agent.sh --status</code>{' '}
          and <code className="rounded bg-slate-800 px-1">tail -20 upgrade-status/upgrade.log</code>.
        </p>
      )}
    </div>
  );
}
