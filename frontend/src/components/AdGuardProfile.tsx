import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type AdGuardProfileName, type AdGuardProfiles } from '../lib/api';
import { usePolling } from '../lib/usePolling';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';

const select = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';

export const PROFILE_LABEL: Record<AdGuardProfileName, string> = { default: 'Default', no_blocking: 'No blocking', strict: 'Strict' };
export const PROFILE_HELP: Record<AdGuardProfileName, string> = {
  default: "AdGuard Home's global settings.",
  no_blocking: 'Nothing is blocked: no blocklists, blocked services, safe browsing or parental control.',
  strict: 'Blocklists plus safe browsing, parental control and safe search (including YouTube Restricted Mode).',
};

/** The per-device and per-group settings, while AdGuard Home actions are on; else null. */
export function useProfiles() {
  const q = usePolling(api.adguardProfiles, 60_000);
  const ok = !q.error && !!q.data && Array.isArray(q.data.managed) && !!q.data.profiles;
  return { profiles: ok ? q.data : null, refresh: q.refresh };
}

function profileConfirm(target: string, profile: AdGuardProfileName, inherits: string | null, run: () => Promise<void>): Confirm {
  return {
    title: profile === 'default' ? `Remove ${target}'s own AdGuard Home settings?` : `Set ${target} to ${PROFILE_LABEL[profile]}?`,
    body: (
      <>
        <p>{profile === 'default' ? `It goes back to ${inherits ?? "AdGuard Home's global settings"}.` : PROFILE_HELP[profile]}</p>
        <p className="text-xs text-slate-400">
          NetWatch keeps a client for this in AdGuard Home (named "NetWatch: …") and updates it when devices change.
          Your own AdGuard Home clients are never changed. The change is written to the security log.
        </p>
      </>
    ),
    confirmLabel: profile === 'default' ? 'Remove' : 'Set',
    danger: profile === 'no_blocking',
    run,
  };
}

/** Device page: this device's profile, or its group's. */
export function DeviceProfile({ d }: { d: { id: number; label: string; group: { id: number; name: string } | null } }) {
  const { profiles, refresh } = useProfiles();
  const [ask, setAsk] = useState<Confirm | null>(null);
  if (!profiles) return null;
  const own = profiles.profiles.devices[String(d.id)];
  const groupProfile = d.group ? profiles.profiles.groups[String(d.group.id)] : undefined;
  const inherits = d.group && groupProfile ? `the ${d.group.name} group's setting (${PROFILE_LABEL[groupProfile]})` : null;
  const conflict = profiles.conflicts.find((c) => c.device_id === d.id);
  const value = own ?? 'default';
  return (
    <section className="space-y-2 rounded-lg bg-slate-900 p-4 text-sm ring-1 ring-slate-800" aria-label="AdGuard Home settings">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">AdGuard Home settings</h2>
      <label className="flex flex-wrap items-center gap-2 text-slate-300">
        Filtering for this device
        <select aria-label="AdGuard Home profile" className={select} value={value}
          onChange={(e) => {
            const p = e.target.value as AdGuardProfileName;
            setAsk(profileConfirm(d.label, p, inherits, async () => { await api.setDeviceProfile(d.id, p); await refresh(); }));
          }}>
          <option value="default">{inherits ? `Use group: ${PROFILE_LABEL[groupProfile!]}` : 'Default (global settings)'}</option>
          <option value="no_blocking">No blocking</option>
          <option value="strict">Strict</option>
        </select>
      </label>
      <p className="text-xs text-slate-500">{PROFILE_HELP[own ?? groupProfile ?? 'default']}</p>
      {conflict && (
        <p className="text-xs text-amber-300">
          Not applied: this device is already in your AdGuard Home client "{conflict.client}". Remove it there to let NetWatch manage it.
        </p>
      )}
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </section>
  );
}

/** Group manager row: the group's profile. */
export function GroupProfileSelect({ group, profiles, onChanged, ask }: {
  group: { id: number; name: string }; profiles: AdGuardProfiles; onChanged: () => Promise<void>; ask: (c: Confirm) => void;
}) {
  const value = profiles.profiles.groups[String(group.id)] ?? 'default';
  return (
    <select aria-label={`AdGuard Home profile for ${group.name}`} className={`${select} py-0.5 text-xs`} value={value}
      title="AdGuard Home settings for this group"
      onChange={(e) => {
        const p = e.target.value as AdGuardProfileName;
        ask(profileConfirm(`the ${group.name} group`, p, null, async () => { await api.setGroupProfile(group.id, p); await onChanged(); }));
      }}>
      <option value="default">AdGuard: default</option>
      <option value="no_blocking">AdGuard: no blocking</option>
      <option value="strict">AdGuard: strict</option>
    </select>
  );
}

/** DNS page: the clients NetWatch keeps in AdGuard Home, and conflicts. */
export function ProfileList() {
  const { profiles } = useProfiles();
  if (!profiles) return null;
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="Per-device and per-group settings">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Per-device and per-group settings</h2>
      {profiles.managed.length === 0 ? (
        <p className="text-sm text-slate-400">None yet. Choose a profile for a group in Devices → Groups…, or for one device on its page.</p>
      ) : (
        <ul className="divide-y divide-slate-800 text-sm">
          {profiles.managed.map((c) => (
            <li key={c.key} className="py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-100">{c.name}</span>
                <span className={`rounded px-1.5 text-[11px] ${c.profile === 'strict' ? 'bg-sky-950 text-sky-200' : 'bg-amber-950 text-amber-200'}`}>{PROFILE_LABEL[c.profile]}</span>
              </div>
              <p className="text-xs text-slate-400">
                {c.devices.length ? c.devices.map((x, i) => (
                  <span key={x.id}>{i > 0 && ', '}<Link to={`/devices/${x.id}`} className="text-sky-400 hover:underline">{x.label}</Link></span>
                )) : 'No devices yet: the client is added when a device joins.'}
              </p>
            </li>
          ))}
        </ul>
      )}
      {profiles.conflicts.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-amber-300">
          {profiles.conflicts.map((c, i) => (
            <li key={i}>
              {c.device_id ? `${c.label} is already in your AdGuard Home client "${c.client}", so it is not managed here.` : `A client named "${c.client}" already exists in AdGuard Home; rename it to let NetWatch use that name.`}
            </li>
          ))}
        </ul>
      )}
      {profiles.error && <p className="mt-2 text-xs text-rose-300">Last sync failed: {profiles.error}</p>}
    </section>
  );
}
