import { useEffect, useState } from 'react';
import { api, type DecoUnit } from '../lib/api';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';

/** The confirmation for restarting one Deco, or every Deco when `deco` is null. */
export function restartConfirm(deco: DecoUnit | { name: string; role?: string } | null, run: () => Promise<void>): Confirm {
  if (!deco) {
    return {
      title: 'Restart the whole Wi-Fi network?',
      body: (
        <p>
          Every Deco restarts, the satellites first and the main Deco last. Wi-Fi is down for a few minutes and every device
          reconnects on its own. This screen may stop answering until the network is back.
        </p>
      ),
      confirmLabel: 'Restart all Decos',
      danger: true,
      run,
    };
  }
  return {
    title: `Restart ${deco.name}?`,
    body:
      deco.role === 'main' ? (
        <p>
          This is the main Deco. Wi-Fi drops for about two minutes on the whole network, and NetWatch may lose its connection
          if it sits behind this Deco.
        </p>
      ) : (
        <p>
          Wi-Fi on {deco.name} drops for about two minutes. Devices on it reconnect on their own, to another Deco if one is in
          range.
        </p>
      ),
    confirmLabel: `Restart ${deco.name}`,
    danger: true,
    run,
  };
}

/** "Restart this Deco" beside a diagnosis cause. Shown only when Deco actions are on in Admin and the Deco is online. */
export function DecoRestartButton({ mac, name }: { mac: string; name: string }) {
  const [unit, setUnit] = useState<DecoUnit | null>(null);
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.deco().then(
      (s) => {
        if (live && s.actions_enabled) setUnit(s.decos.find((d) => d.mac === mac && d.online) ?? null);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [mac]);

  if (!unit) return null;
  return (
    <div className="mt-2">
      <button
        className={`${btn} text-rose-300 ring-1 ring-slate-700 hover:bg-slate-800`}
        onClick={() =>
          setAsk(
            restartConfirm(unit, async () => {
              await api.rebootDeco(unit.mac);
              setNote(`Restarting ${name}. It should be back in about two minutes.`);
            }),
          )
        }
      >
        Restart {name}
      </button>
      {note && <p className="mt-1 text-emerald-300" role="status">{note}</p>}
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </div>
  );
}
