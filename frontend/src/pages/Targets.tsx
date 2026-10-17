import { useState } from 'react';
import { Link } from 'react-router-dom';
import { EMPTY_TARGET, TargetForm } from '../components/TargetForm';
import { api, toInput, type Target, type TargetInput } from '../lib/api';
import { GROUP_LABEL } from '../lib/format';
import { usePolling } from '../lib/usePolling';

export function Targets() {
  const { data, error, refresh } = usePolling(api.targets, 30_000);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>) {
    setActionError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }

  const create = async (v: TargetInput) => {
    await api.createTarget(v);
    setAdding(false);
    await refresh();
  };
  const save = (t: Target) => async (v: TargetInput) => {
    await api.updateTarget(t.id, v);
    setEditing(null);
    await refresh();
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Targets</h1>
        {!adding && (
          <button onClick={() => setAdding(true)} className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500">
            Add target
          </button>
        )}
      </div>

      {adding && (
        <div className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800">
          <TargetForm initial={EMPTY_TARGET} submitLabel="Add target" onSubmit={create} onCancel={() => setAdding(false)} />
        </div>
      )}

      {error && <p className="text-rose-400">Could not load targets: {error}</p>}
      {actionError && <p role="alert" className="text-rose-400">{actionError}</p>}

      {data && (
        <div className="overflow-x-auto rounded-lg ring-1 ring-slate-800">
          <table className="min-w-full divide-y divide-slate-800 text-sm">
            <thead className="bg-slate-900 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Probe</th>
                <th className="px-3 py-2">Target</th>
                <th className="px-3 py-2">Interval</th>
                <th className="px-3 py-2">Group</th>
                <th className="px-3 py-2">Enabled</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {data.map((t) =>
                editing === t.id ? (
                  <tr key={t.id} className="bg-slate-900/60">
                    <td colSpan={7} className="p-4">
                      <TargetForm initial={toInput(t)} submitLabel="Save" onSubmit={save(t)} onCancel={() => setEditing(null)} />
                    </td>
                  </tr>
                ) : (
                  <tr key={t.id} className="hover:bg-slate-900/50">
                    <td className="px-3 py-2">
                      <Link to={`/targets/${t.id}`} className="text-sky-300 hover:underline">{t.name}</Link>
                      {t.auto && <span className="ml-2 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">auto</span>}
                    </td>
                    <td className="px-3 py-2 uppercase text-slate-400">{t.kind}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-300">
                      {t.kind === 'http' ? t.url : t.port ? `${t.host}:${t.port}` : t.host}
                      {t.kind === 'dns' && <span className="text-slate-500"> ({t.dns_query})</span>}
                    </td>
                    <td className="px-3 py-2 text-slate-300">{t.interval_s}s</td>
                    <td className="px-3 py-2 text-slate-400">{GROUP_LABEL[t.group] ?? t.group}</td>
                    <td className="px-3 py-2">
                      <input
                        type="checkbox"
                        aria-label={`Enable ${t.name}`}
                        checked={t.enabled}
                        onChange={() => act(() => api.updateTarget(t.id, { ...toInput(t), enabled: !t.enabled }))}
                      />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right">
                      <button onClick={() => setEditing(t.id)} className="px-2 text-slate-400 hover:text-white">Edit</button>
                      <button
                        onClick={() => {
                          if (window.confirm(`Delete "${t.name}" and all of its history?`)) void act(() => api.deleteTarget(t.id));
                        }}
                        className="px-2 text-rose-400 hover:text-rose-300"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
          {data.length === 0 && <p className="p-6 text-center text-slate-500">No targets yet.</p>}
        </div>
      )}
    </div>
  );
}
