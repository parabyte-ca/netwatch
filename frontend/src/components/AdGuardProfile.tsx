import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type AdGuardProfileName, type AdGuardProfiles, type AdGuardSettings, type YouTubeSetting } from '../lib/api';
import { usePolling } from '../lib/usePolling';
import { ConfirmDialog, type Confirm } from './ConfirmDialog';
import { TargetSchedules } from './Schedules';

const select = 'rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100 focus:border-sky-500 focus:outline-none';
const btn = 'rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50';

export const DEFAULT_SETTINGS: AdGuardSettings = { filtering: 'default', youtube: 'default', services: [] };
export const PROFILE_LABEL: Record<AdGuardProfileName, string> = { default: 'Default', no_blocking: 'No blocking', strict: 'Strict' };
const FILTER_HELP: Record<AdGuardProfileName, string> = {
  default: "AdGuard Home's global filtering, safe browsing and parental control.",
  no_blocking: 'Nothing is blocked except the apps chosen here.',
  strict: 'Blocklists plus safe browsing, parental control and safe search.',
};
const YOUTUBE_LABEL: Record<YouTubeSetting, string> = {
  default: 'As set above', restricted: 'Restricted Mode', blocked: 'Blocked', allowed: 'Allowed (even if blocked for everyone)',
};
const YOUTUBE_SHORT: Record<Exclude<YouTubeSetting, 'default'>, string> = {
  restricted: 'YouTube Restricted Mode', blocked: 'YouTube blocked', allowed: 'YouTube allowed',
};

const same = (a: AdGuardSettings, b: AdGuardSettings) =>
  a.filtering === b.filtering && a.youtube === b.youtube && [...a.services].sort().join() === [...b.services].sort().join();
export const isDefault = (s: AdGuardSettings) => same(s, DEFAULT_SETTINGS);

/** "Strict, YouTube blocked, also blocks TikTok", like the security log. */
export function describe(s: AdGuardSettings, names: Record<string, string> = {}): string {
  const parts: string[] = [];
  if (s.filtering !== 'default') parts.push(PROFILE_LABEL[s.filtering]);
  if (s.youtube !== 'default') parts.push(YOUTUBE_SHORT[s.youtube]);
  if (s.services.length) {
    if (!parts.length) parts.push('Default filtering');
    parts.push(`also blocks ${s.services.map((x) => names[x] ?? x).join(', ')}`);
  }
  return parts.join(', ') || 'Default';
}

/** The per-device and per-group settings, while AdGuard Home actions are on; else null. */
export function useProfiles() {
  const q = usePolling(api.adguardProfiles, 60_000);
  const ok = !q.error && !!q.data && Array.isArray(q.data.managed) && !!q.data.profiles;
  return { profiles: ok ? q.data : null, refresh: q.refresh };
}

const namesOf = (p: AdGuardProfiles) => Object.fromEntries((p.services_all ?? []).map((x) => [x.id, x.name]));

function settingsConfirm(target: string, s: AdGuardSettings, names: Record<string, string>, fallback: string | null, run: () => Promise<void>): Confirm {
  const off = isDefault(s);
  return {
    title: off ? `Remove ${target}'s own AdGuard Home settings?` : `Set ${target} to ${describe(s, names)}?`,
    body: (
      <>
        <p>{off ? `It goes back to ${fallback ?? "AdGuard Home's global settings"}.` : FILTER_HELP[s.filtering]}</p>
        {s.youtube === 'restricted' && <p>YouTube hides mature videos and comments (Restricted Mode).</p>}
        <p className="text-xs text-slate-400">
          NetWatch keeps a client for this in AdGuard Home (named "NetWatch: …") and updates it when devices change.
          Your own AdGuard Home clients are never changed. The change is written to the security log.
        </p>
      </>
    ),
    confirmLabel: off ? 'Remove' : 'Set',
    danger: s.filtering === 'no_blocking' || s.youtube === 'allowed',
    run,
  };
}

/** Filtering, YouTube and extra apps to block. */
export function SettingsEditor({ value, onChange, services, idPrefix }: {
  value: AdGuardSettings; onChange: (s: AdGuardSettings) => void; services: { id: string; name: string }[]; idPrefix: string;
}) {
  const names = Object.fromEntries(services.map((x) => [x.id, x.name]));
  const left = services.filter((x) => x.id !== 'youtube' && !value.services.includes(x.id)).sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-400">Filtering
          <select aria-label={`${idPrefix} filtering`} className={select} value={value.filtering}
            onChange={(e) => onChange({ ...value, filtering: e.target.value as AdGuardProfileName })}>
            <option value="default">Default (global settings)</option>
            <option value="no_blocking">No blocking</option>
            <option value="strict">Strict</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">YouTube
          <select aria-label={`${idPrefix} YouTube`} className={select} value={value.youtube}
            onChange={(e) => onChange({ ...value, youtube: e.target.value as YouTubeSetting })}>
            {(Object.keys(YOUTUBE_LABEL) as YouTubeSetting[]).map((k) => <option key={k} value={k}>{YOUTUBE_LABEL[k]}</option>)}
          </select>
        </label>
      </div>
      <p className="text-xs text-slate-500">{FILTER_HELP[value.filtering]}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-slate-400">Also block:</span>
        {value.services.map((id) => (
          <span key={id} className="inline-flex items-center gap-1 rounded-full bg-rose-950 px-2 py-0.5 text-xs text-rose-200 ring-1 ring-rose-900">
            {names[id] ?? id}
            <button aria-label={`Stop blocking ${names[id] ?? id}`} className="hover:text-white"
              onClick={() => onChange({ ...value, services: value.services.filter((x) => x !== id) })}>×</button>
          </span>
        ))}
        {left.length > 0 && (
          <select aria-label={`${idPrefix} add an app to block`} className={`${select} py-0.5 text-xs`} value=""
            onChange={(e) => e.target.value && onChange({ ...value, services: [...value.services, e.target.value] })}>
            <option value="">Add an app…</option>
            {left.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        )}
      </div>
    </div>
  );
}

/** Device page: this device's own settings, or its group's. */
export function DeviceProfile({ d }: { d: { id: number; label: string; group: { id: number; name: string } | null } }) {
  const { profiles, refresh } = useProfiles();
  const [ask, setAsk] = useState<Confirm | null>(null);
  const [draft, setDraft] = useState<AdGuardSettings | null>(null);
  if (!profiles) return null;
  const names = namesOf(profiles);
  const own = profiles.profiles.devices[String(d.id)];
  const groupSettings = d.group ? profiles.profiles.groups[String(d.group.id)] : undefined;
  const fallback = d.group && groupSettings ? `the ${d.group.name} group's settings (${describe(groupSettings, names)})` : null;
  const conflict = profiles.conflicts.find((c) => c.device_id === d.id);
  const value = draft ?? own ?? DEFAULT_SETTINGS;
  const save = (s: AdGuardSettings) =>
    setAsk(settingsConfirm(d.label, s, names, fallback, async () => { await api.setDeviceProfile(d.id, s); setDraft(null); await refresh(); }));
  return (
    <section className="space-y-2 rounded-lg bg-slate-900 p-4 text-sm ring-1 ring-slate-800" aria-label="AdGuard Home settings">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">AdGuard Home settings</h2>
      <p className="text-slate-300">
        Now: <span className="font-medium text-slate-100">{own ? describe(own, names) : groupSettings ? describe(groupSettings, names) : 'Default'}</span>
        {!own && fallback && <span className="text-xs text-slate-500"> (from the {d.group!.name} group)</span>}
      </p>
      <SettingsEditor value={value} onChange={setDraft} services={profiles.services_all ?? []} idPrefix="Device" />
      <div className="flex flex-wrap gap-2">
        <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={same(value, own ?? DEFAULT_SETTINGS)}
          onClick={() => save(value)}>Save</button>
        {own && <button className={`${btn} text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800`} onClick={() => save(DEFAULT_SETTINGS)}>
          {fallback ? "Use the group's settings" : 'Remove own settings'}</button>}
      </div>
      {conflict && (
        <p className="text-xs text-amber-300">
          Not applied: this device is already in your AdGuard Home client "{conflict.client}". Remove it there to let NetWatch manage it.
        </p>
      )}
      <div className="border-t border-slate-800 pt-3">
        <TargetSchedules target={{ kind: 'device', id: d.id }} label={d.label} services={profiles.services_all ?? []} />
      </div>
      {ask && <ConfirmDialog confirm={ask} onClose={() => setAsk(null)} />}
    </section>
  );
}

/** Group manager row: the group's settings, edited in a small dialog. */
export function GroupProfileButton({ group, profiles, onChanged, ask }: {
  group: { id: number; name: string }; profiles: AdGuardProfiles; onChanged: () => Promise<void>; ask: (c: Confirm) => void;
}) {
  const cur = profiles.profiles.groups[String(group.id)] ?? DEFAULT_SETTINGS;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<AdGuardSettings>(cur);
  const names = namesOf(profiles);
  return (
    <>
      <button className="max-w-[12rem] truncate text-xs text-sky-400 hover:underline" title={`AdGuard Home: ${describe(cur, names)}`}
        onClick={() => { setDraft(cur); setOpen(true); }}>
        AdGuard Home{isDefault(cur) ? '…' : `: ${describe(cur, names)}`}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4" onClick={() => setOpen(false)}>
          <div role="dialog" aria-modal="true" aria-label={`AdGuard Home settings for ${group.name}`} onClick={(e) => e.stopPropagation()}
            className="mt-16 w-full max-w-md space-y-3 rounded-lg bg-slate-900 p-5 ring-1 ring-slate-700">
            <h3 className="font-semibold text-white">AdGuard Home settings for {group.name}</h3>
            <SettingsEditor value={draft} onChange={setDraft} services={profiles.services_all ?? []} idPrefix={group.name} />
            <div className="flex justify-end gap-2">
              <button className={`${btn} text-slate-300 ring-1 ring-slate-700`} onClick={() => setOpen(false)}>Cancel</button>
              <button className={`${btn} bg-sky-600 text-white hover:bg-sky-500`} disabled={same(draft, cur)}
                onClick={() => { setOpen(false); ask(settingsConfirm(`the ${group.name} group`, draft, names, null, async () => { await api.setGroupProfile(group.id, draft); await onChanged(); })); }}>
                Save
              </button>
            </div>
            <div className="border-t border-slate-800 pt-3">
              <TargetSchedules target={{ kind: 'group', id: group.id }} label={group.name} services={profiles.services_all ?? []} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/** DNS page: the clients NetWatch keeps in AdGuard Home, and conflicts. */
export function ProfileList() {
  const { profiles } = useProfiles();
  if (!profiles) return null;
  const names = namesOf(profiles);
  return (
    <section className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800" aria-label="Per-device and per-group settings">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">Per-device and per-group settings</h2>
      {profiles.managed.length === 0 ? (
        <p className="text-sm text-slate-400">None yet. Choose settings for a group in Devices → Groups…, or for one device on its page.</p>
      ) : (
        <ul className="divide-y divide-slate-800 text-sm">
          {profiles.managed.map((c) => (
            <li key={c.key} className="py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-100">{c.name}</span>
                <span className={`rounded px-1.5 text-[11px] ${c.profile.filtering === 'no_blocking' ? 'bg-amber-950 text-amber-200' : 'bg-sky-950 text-sky-200'}`}>{describe(c.profile, names)}</span>
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
