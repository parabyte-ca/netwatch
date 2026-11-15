"""AdGuard Home settings per device and per group, kept as persistent clients NetWatch owns.

A device or a group gets settings: filtering (default, no_blocking or strict), YouTube (default,
restricted, blocked or allowed) and extra apps to block. For each one that is not all default,
NetWatch keeps a persistent client named "NetWatch: <name>" holding the devices' MACs.
A device's own profile beats its group's. NetWatch only adds, changes or deletes the clients whose
names it tracks; a MAC that is already in one of your own clients is left out and reported.
Runs through AdGuardActions, so nothing happens unless "AdGuard Home actions" is on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time

from ..events import DNS
from .actions import ActionError
from .client import norm_mac
from .schedules import describe_effects, overlay

log = logging.getLogger(__name__)

PROFILES_KEY = "adguard_profiles"  # {"devices": {id: settings}, "groups": {id: settings}}
TRACKED_KEY = "adguard_profile_clients"  # {"device:5" | "group:3": client name}
PROFILES = ("default", "no_blocking", "strict")
PROFILE_LABEL = {"default": "Default", "no_blocking": "No blocking", "strict": "Strict"}
YOUTUBE = ("default", "restricted", "blocked", "allowed")
YOUTUBE_LABEL = {"restricted": "YouTube Restricted Mode", "blocked": "YouTube blocked",
                 "allowed": "YouTube allowed"}  # fmt: skip
DEFAULT_SETTINGS = {"filtering": "default", "youtube": "default", "services": []}
MAX_SERVICES = 30
SERVICES_TTL_S = 3600
_SERVICE_ID = re.compile(r"^[a-z0-9_]{1,64}$")
PREFIX = "NetWatch: "
SYNC_S = 15  # how soon a group change reaches AdGuard Home
FULL_SYNC_S = 300  # also re-check everything this often (drift)
SCHEDULE_RULES_KEY = "adguard_schedule_rules"  # the $client lines NetWatch added for "internet off"
_ENGINES = ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex", "youtube")


def normalize_settings(v) -> dict:
    """Stored or sent settings as a full dict; a v0.17 profile string is its filtering."""
    if isinstance(v, str):
        v = {"filtering": v}
    if not isinstance(v, dict):
        return dict(DEFAULT_SETTINGS)
    f = v.get("filtering") if v.get("filtering") in PROFILES else "default"
    y = v.get("youtube") if v.get("youtube") in YOUTUBE else "default"
    svc = sorted({str(s) for s in v.get("services") or [] if _SERVICE_ID.match(str(s))})
    return {"filtering": f, "youtube": y, "services": svc[:MAX_SERVICES]}


def is_default(s: dict) -> bool:
    return normalize_settings(s) == DEFAULT_SETTINGS


def describe(s: dict, names: dict[str, str] | None = None) -> str:
    """ "Strict, YouTube blocked, also blocks TikTok" for the security log and the UI."""
    s = normalize_settings(s)
    parts = [PROFILE_LABEL[s["filtering"]]] if s["filtering"] != "default" else []
    if s["youtube"] != "default":
        parts.append(YOUTUBE_LABEL[s["youtube"]])
    if s["services"]:
        parts.append("also blocks " + ", ".join((names or {}).get(x, x) for x in s["services"]))
    if s["filtering"] == "default" and s["services"] and s["youtube"] == "default":
        parts.insert(0, "Default filtering")
    return ", ".join(parts) or "Default"


GLOBAL_OFF = {"filtering": True, "safebrowsing": False, "parental": False,
              "safesearch": {"enabled": False}, "blocked": []}  # fmt: skip


def client_body(name: str, settings: dict, macs: list[str], g: dict | None = None) -> dict:
    """The persistent client AdGuard Home should have for these settings.

    ``g`` is AdGuard Home's global protection (filtering, safe browsing, parental control, safe
    search and blocked apps): "default" filtering copies it, so only YouTube or apps change.
    """
    s = normalize_settings(settings)
    g = g or GLOBAL_OFF
    f = s["filtering"]
    if f in ("strict", "no_blocking"):
        on = f == "strict"
        filt = par = sb = on
        ss = {"enabled": on, **{e: on for e in _ENGINES}}
    else:
        filt, par, sb = bool(g["filtering"]), bool(g["parental"]), bool(g["safebrowsing"])
        gss = g.get("safesearch") or {}
        on = bool(gss.get("enabled"))
        ss = {"enabled": on, **{e: on and bool(gss.get(e, True)) for e in _ENGINES}}
    if s["youtube"] == "restricted":
        if not ss["enabled"]:
            ss = {"enabled": True, **{e: False for e in _ENGINES}}
        ss["youtube"] = True
    elif s["youtube"] == "allowed":
        # Safe search for YouTube is Restricted Mode (it hides comments): allowed means without it.
        ss["youtube"] = False
    base = [] if f == "no_blocking" else list(g.get("blocked") or [])
    blocked = set(base) | set(s["services"])
    if s["youtube"] == "blocked":
        blocked.add("youtube")
    elif s["youtube"] == "allowed":
        blocked.discard("youtube")
    use_global = f != "no_blocking" and blocked == set(base)
    return {
        "name": name,
        "ids": sorted(macs),
        "use_global_settings": False,
        "filtering_enabled": filt,
        "parental_enabled": par,
        "safebrowsing_enabled": sb,
        "safesearch": ss,
        "safesearch_enabled": ss["enabled"],  # AdGuard Home before v0.107.28
        "use_global_blocked_services": use_global,
        "blocked_services": sorted(blocked),
        "upstreams": [],
        "tags": [],
        "ignore_querylog": False,
        "ignore_statistics": False,
    }


def _ids(c: dict) -> set[str]:
    out = set()
    for i in c.get("ids") or []:
        m = norm_mac(str(i))
        out.add(m or str(i).strip().lower())
    return out


def _differs(cur: dict, want: dict) -> bool:
    if _ids(cur) != set(want["ids"]):
        return True
    for k in ("use_global_settings", "filtering_enabled", "parental_enabled",
              "safebrowsing_enabled", "use_global_blocked_services"):  # fmt: skip
        if bool(cur.get(k)) != want[k]:
            return True
    ss = cur.get("safesearch")
    on = ss.get("enabled") if isinstance(ss, dict) else cur.get("safesearch_enabled")
    if bool(on) != want["safesearch"]["enabled"]:
        return True
    if isinstance(ss, dict) and bool(ss.get("youtube")) != want["safesearch"]["youtube"]:
        return True
    if not want["use_global_blocked_services"]:
        bs = cur.get("blocked_services")
        bs = bs.get("ids") if isinstance(bs, dict) else bs
        return sorted(bs or []) != want["blocked_services"]
    return False


def internet_off_rule(client: str) -> str:
    """Block every lookup for one AdGuard Home client, ahead of allow rules ($important)."""
    esc = "".join("\\" + c if c in "\\'\",|" else c for c in client)
    return f"||*^$client='{esc}',important"


def client_name(label: str) -> str:
    clean = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", "", label or "")).strip()
    return (PREFIX + (clean or "device"))[:64].rstrip()


class ProfileSync:
    def __init__(self, actions, inventory, groups, schedules=None) -> None:
        self.actions = actions  # AdGuardActions: the on/off gate and the client
        self.inventory = inventory
        self.groups = groups
        self.schedules = schedules  # ScheduleStore, or None
        self._events: dict[str, tuple[int, int]] = {}  # active schedule → (event id, planned end)
        self._last_sig: tuple | None = None
        self.store = actions.store
        self.conflicts: list[dict] = []
        self.last_sync_ms: int | None = None
        self.error: str | None = None
        self.dirty = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._services: tuple[float, list[dict]] | None = None

    # ── stored choices ───────────────────────────────────────────────────────

    def profiles(self) -> dict:
        """Every stored choice as full settings (v0.17 profile strings are read too)."""
        p = self.store.kv_get(PROFILES_KEY, None) or {}
        out: dict[str, dict] = {"devices": {}, "groups": {}}
        for kind in out:
            for k, v in (p.get(kind) or {}).items():
                s = normalize_settings(v)
                if not is_default(s):
                    out[kind][str(k)] = s
        return out

    def tracked(self) -> dict[str, str]:
        t = self.store.kv_get(TRACKED_KEY, None) or {}
        return {str(k): str(v) for k, v in t.items()} if isinstance(t, dict) else {}

    def _save(self, kind: str, key: int, settings: dict) -> None:
        p = self.profiles()
        if is_default(settings):
            p[kind].pop(str(key), None)
        else:
            p[kind][str(key)] = normalize_settings(settings)
        self.store.kv_set(PROFILES_KEY, p)

    def check_settings(self, raw) -> dict:
        """Settings sent by the UI: known values only, at most 30 apps."""
        if isinstance(raw, str):
            raw = {"filtering": raw}
        if not isinstance(raw, dict):
            raise ActionError("settings are missing")
        if raw.get("filtering", "default") not in PROFILES:
            raise ActionError("profile must be default, no_blocking or strict")
        if raw.get("youtube", "default") not in YOUTUBE:
            raise ActionError("YouTube must be default, restricted, blocked or allowed")
        svc = list(raw.get("services") or [])
        if len(svc) > MAX_SERVICES or any(not _SERVICE_ID.match(str(x)) for x in svc):
            raise ActionError(f"choose at most {MAX_SERVICES} apps from AdGuard Home's list")
        return normalize_settings(raw)

    async def services_all(self, client=None) -> list[dict]:
        """AdGuard Home's list of apps it can block ([{id, name}]), cached for an hour."""
        now = time.monotonic()
        if self._services and now - self._services[0] < SERVICES_TTL_S:
            return self._services[1]
        client = client or await self.actions._client()
        items = await self.actions._call(client.blocked_services_all())
        self._services = (now, items)
        return items

    def has_work(self) -> bool:
        p = self.profiles()
        scheduled = bool(self.schedules and (self.schedules.list() or self.schedules.active()))
        return bool(p["devices"] or p["groups"] or self.tracked() or scheduled or self._events)

    def active_signature(self) -> tuple:
        """What schedules are in force now; a change means AdGuard Home needs updating."""
        if not self.schedules:
            return ()
        return tuple(sorted((a["id"], a["target"]["kind"], a["target"]["id"])
                            for a in self.schedules.active()))  # fmt: skip

    # ── what AdGuard Home should hold ────────────────────────────────────────

    def desired(self, now_ms: int | None = None) -> dict[str, dict]:
        """key → {name, profile (settings in force now), base, internet_off, active, label,
        devices: [{id, label, macs}]}; prunes stale choices."""
        now = now_ms or int(time.time() * 1000)
        p = self.profiles()
        rows = self.inventory.listing(now)
        ids = {d["id"] for d in rows}
        groups = {g["id"]: g for g in self.groups.list()}
        dev = {int(k): v for k, v in p["devices"].items() if int(k) in ids}
        grp = {int(k): v for k, v in p["groups"].items() if int(k) in groups}
        pruned = {"devices": {str(k): v for k, v in dev.items()},
                  "groups": {str(k): v for k, v in grp.items()}}  # fmt: skip
        if pruned != p:
            self.store.kv_set(PROFILES_KEY, pruned)
        # Schedules: their targets need a client even with default settings; deleted groups
        # and devices take their schedules with them.
        sched_g: set[int] = set()
        sched_d: set[int] = set()
        effects: dict[tuple[str, int], list[dict]] = {}
        names: dict[tuple[str, int], list[str]] = {}
        if self.schedules is not None:
            for s in self.schedules.list():
                kind, tid = s["target"]["kind"], s["target"]["id"]
                if (kind == "group" and tid not in groups) or (kind == "device" and tid not in ids):
                    self.schedules.drop_target(kind, tid)
                    continue
                if s.get("enabled", True):
                    (sched_g if kind == "group" else sched_d).add(tid)
            for a in self.schedules.active(now):
                k = (a["target"]["kind"], a["target"]["id"])
                (sched_g if k[0] == "group" else sched_d).add(k[1])
                effects.setdefault(k, []).append(a["effects"])
                names.setdefault(k, []).append(a["name"])
        out: dict[str, dict] = {}
        for gid in set(grp) | (sched_g & set(groups)):
            g = groups[gid]
            out[f"group:{gid}"] = {"name": client_name(g["name"]), "label": g["name"],
                                   "base": grp.get(gid) or normalize_settings({}),
                                   "fx": effects.get(("group", gid), []),
                                   "active": names.get(("group", gid), []),
                                   "devices": []}  # fmt: skip
        for d in rows:
            entry = {"id": d["id"], "label": d["label"], "macs": list(d["macs"])}
            gid = d.get("group_id")
            if d["id"] in dev or d["id"] in sched_d:
                # Its own client: its own settings, else its group's; its group's schedules too.
                out[f"device:{d['id']}"] = {
                    "name": client_name(d["label"]),
                    "label": d["label"],
                    "base": dev.get(d["id"]) or grp.get(gid) or normalize_settings({}),
                    "fx": effects.get(("device", d["id"]), []) + effects.get(("group", gid), []),
                    "active": names.get(("device", d["id"]), []) + names.get(("group", gid), []),
                    "devices": [entry],
                }
            elif f"group:{gid}" in out:
                out[f"group:{gid}"]["devices"].append(entry)
        for e in out.values():
            e["profile"], e["internet_off"] = overlay(e["base"], e.pop("fx"))
        # Names must be unique in AdGuard Home: a device sharing a name gets its id added.
        seen: set[str] = set()
        for key in sorted(out, key=lambda k: (not k.startswith("group:"), k)):
            name = out[key]["name"]
            if name in seen:
                name = f"{name[:56]} #{key.split(':')[1]}"
                out[key]["name"] = name
            seen.add(name)
        return out

    # ── bringing AdGuard Home in line ────────────────────────────────────────

    async def reconcile(self) -> dict:
        """Make AdGuard Home's NetWatch clients match the choices. Returns what changed."""
        async with self._lock:
            self.dirty = False
            client = await self.actions._client()  # ActionsDisabled when actions are off
            tracked = await asyncio.to_thread(self.tracked)
            desired = await asyncio.to_thread(self.desired)
            lines_had = await asyncio.to_thread(self.store.kv_get, SCHEDULE_RULES_KEY, [])
            if not desired and not tracked and not lines_had and not self._events:
                self.conflicts, self.error = [], None
                self.last_sync_ms = int(time.time() * 1000)
                return {"changes": [], "conflicts": []}
            raw = await self.actions._call(client.persistent_clients())
            g = await self.actions._call(client.global_protection()) if desired else None
            by_name = {c["name"]: c for c in raw}
            ours = set(tracked.values())
            foreign: dict[str, str] = {}  # mac → the user's own client holding it
            for c in raw:
                if c["name"] not in ours:
                    for i in _ids(c):
                        foreign[i] = c["name"]

            conflicts: list[dict] = []
            want: dict[str, dict] = {}
            for key, d in desired.items():
                macs: list[str] = []
                for dev in d["devices"]:
                    taken = [m for m in dev["macs"] if m in foreign]
                    if taken:
                        conflicts.append({"device_id": dev["id"], "label": dev["label"],
                                          "mac": taken[0], "client": foreign[taken[0]],
                                          "profile": d["profile"]})  # fmt: skip
                        continue
                    macs.extend(dev["macs"])
                if d["name"] in by_name and d["name"] not in ours:
                    conflicts.append({"device_id": None, "label": d["label"], "mac": None,
                                      "client": d["name"], "profile": d["profile"]})  # fmt: skip
                    continue
                if macs:
                    want[key] = client_body(d["name"], d["profile"], macs, g)

            changes: list[dict] = []

            async def call(coro):
                await self.actions._call(coro)

            # 1. Clients that should go, and MACs leaving a client, first: AdGuard Home refuses
            #    a MAC in two clients, so nothing is added before it is free.
            for key, name in list(tracked.items()):
                cur = by_name.get(name)
                if key not in want:
                    if cur is not None:
                        await call(client.delete_client(name))
                        changes.append({"action": "removed", "client": name})
                    tracked.pop(key)
                    continue
                if cur is not None:
                    keep = _ids(cur) & set(want[key]["ids"])
                    if keep != _ids(cur):
                        if keep:
                            await call(client.update_client(name, {**cur, "ids": sorted(keep)}))
                            cur["ids"] = sorted(keep)
                            changes.append({"action": "updated", "client": name})
                        else:
                            await call(client.delete_client(name))
                            by_name.pop(name, None)
                            tracked.pop(key)
            # 2. Then add and update.
            for key, body in want.items():
                old = tracked.get(key)
                cur = by_name.get(old) if old else None
                if cur is None:
                    await call(client.add_client(body))
                    changes.append({"action": "added", "client": body["name"]})
                elif old != body["name"] or _differs(cur, body):
                    await call(client.update_client(old, body))
                    if {"action": "updated", "client": old} in changes:
                        changes.remove({"action": "updated", "client": old})
                    changes.append({"action": "updated", "client": body["name"]})
                tracked[key] = body["name"]
                await asyncio.to_thread(self.store.kv_set, TRACKED_KEY, tracked)
            await asyncio.to_thread(self.store.kv_set, TRACKED_KEY, tracked)
            # 3. "Internet off": one $client rule per client while a schedule says so.
            lines = sorted({internet_off_rule(want[k]["name"]) for k, d in desired.items()
                            if d["internet_off"] and k in want})  # fmt: skip
            changes += await self._sync_lines(client, lines, list(lines_had or []))
            await asyncio.to_thread(self._schedule_events, desired)
            self.conflicts, self.error = conflicts, None
            self.last_sync_ms = int(time.time() * 1000)
            return {"changes": changes, "conflicts": conflicts}

    async def _sync_lines(self, client, want: list[str], had: list[str]) -> list[dict]:
        """Add and remove NetWatch's own $client lines; the user's own rules are never touched."""
        if sorted(want) == sorted(had):
            return []
        current = await self.actions._call(client.user_rules())
        new = [r for r in current if r not in had or r in want]
        new += [r for r in want if r not in new]
        if new != current:
            await self.actions._call(client.set_user_rules(new))
        await asyncio.to_thread(self.store.kv_set, SCHEDULE_RULES_KEY, list(want))
        added = [{"action": "internet off", "client": r} for r in want if r not in had]
        return added + [{"action": "internet back on", "client": r} for r in had if r not in want]

    def _schedule_events(self, desired: dict) -> None:
        """One information event per schedule on a group or device, recorded with its planned
        end (so it shows on the timeline, not as an ongoing incident) and shortened if it ends
        early."""
        if self.schedules is None:
            return
        now = int(time.time() * 1000)
        labels = {k: d["label"] for k, d in desired.items()}
        current: dict[str, dict] = {}
        for a in self.schedules.active(now):
            key = f"{a['id']}:{a['target']['kind']}:{a['target']['id']}"
            current[key] = a
        for key, a in current.items():
            if key in self._events:
                continue
            who = labels.get(f"{a['target']['kind']}:{a['target']['id']}", a["target"]["kind"])
            eid = self.actions.events.open(
                DNS, "info", now, f"{who}: {describe_effects(a['effects'])} ({a['name']})",
                details={"type": "schedule", "schedule": a["id"], "target": a["target"],
                         "until_ms": a["end_ms"], "manual": a["manual"]},
                end_ms=a["end_ms"],
            )  # fmt: skip
            self._events[key] = (eid, a["end_ms"])
        for key in [k for k in self._events if k not in current]:
            eid, planned = self._events.pop(key)
            if now < planned:  # ended early: +15 min, back on now, turned off
                self.actions.events.set_end(eid, now)

    async def _check_apps(self, s: dict) -> None:
        if not s["services"]:
            return
        known = {x["id"] for x in await self.services_all()}
        unknown = [x for x in s["services"] if known and x not in known]
        if unknown:
            raise ActionError(f"AdGuard Home does not know the app {unknown[0]}")

    async def set_device(self, device_id: int, settings) -> dict:
        s = self.check_settings(settings)
        await self._check_apps(s)
        prim = (await asyncio.to_thread(self.groups.primaries, [device_id]))[0]
        await asyncio.to_thread(self._save, "devices", prim, s)
        return await self.reconcile()

    async def set_group(self, group_id: int, settings) -> dict:
        s = self.check_settings(settings)
        if await asyncio.to_thread(self.groups.get, group_id) is None:
            raise ActionError("no such group")
        await self._check_apps(s)
        await asyncio.to_thread(self._save, "groups", group_id, s)
        return await self.reconcile()

    def state(self) -> dict:
        return {
            "profiles": self.profiles(),
            "clients": self.tracked(),
            "conflicts": self.conflicts,
            "last_sync_ms": self.last_sync_ms,
            "error": self.error,
        }

    # ── background ───────────────────────────────────────────────────────────

    def mark_dirty(self) -> None:
        self.dirty = True

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-adguard-profiles")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        last_full = 0.0
        while True:
            await asyncio.sleep(SYNC_S)
            now = time.monotonic()
            sig = await asyncio.to_thread(self.active_signature)
            due = self.dirty or now - last_full >= FULL_SYNC_S or sig != self._last_sig
            if not due or not self.actions.enabled() or not self.actions.service.configured:
                continue
            try:
                if await asyncio.to_thread(self.has_work):
                    result = await self.reconcile()
                    last_full = now
                    self._last_sig = sig
                    for ch in result["changes"]:
                        self.actions.events.instant(
                            "security", "info", int(time.time() * 1000),
                            f"AdGuard Home: {ch['action']} {ch['client']} (keeping group and "
                            "device settings in step)",
                            details={"by": "NetWatch", "action": "adguard_profile_sync"},
                        )  # fmt: skip
            except Exception as exc:  # keep the loop alive, show the reason
                self.error = str(exc)[:200]
                log.warning("AdGuard Home profile sync failed: %s", exc)
