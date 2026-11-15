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

from .actions import ActionError
from .client import norm_mac

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


def client_name(label: str) -> str:
    clean = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", "", label or "")).strip()
    return (PREFIX + (clean or "device"))[:64].rstrip()


class ProfileSync:
    def __init__(self, actions, inventory, groups) -> None:
        self.actions = actions  # AdGuardActions: the on/off gate and the client
        self.inventory = inventory
        self.groups = groups
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
        return bool(p["devices"] or p["groups"] or self.tracked())

    # ── what AdGuard Home should hold ────────────────────────────────────────

    def desired(self) -> dict[str, dict]:
        """key → {name, profile (settings), label, devices: [{id, label, macs}]}; prunes."""
        p = self.profiles()
        rows = self.inventory.listing(int(time.time() * 1000))
        ids = {d["id"] for d in rows}
        groups = {g["id"]: g for g in self.groups.list()}
        dev = {int(k): v for k, v in p["devices"].items() if int(k) in ids}
        grp = {int(k): v for k, v in p["groups"].items() if int(k) in groups}
        pruned = {"devices": {str(k): v for k, v in dev.items()},
                  "groups": {str(k): v for k, v in grp.items()}}  # fmt: skip
        if pruned != p:
            self.store.kv_set(PROFILES_KEY, pruned)
        out: dict[str, dict] = {}
        for gid, prof in grp.items():
            g = groups[gid]
            out[f"group:{gid}"] = {"name": client_name(g["name"]), "profile": prof,
                                   "label": g["name"], "devices": []}  # fmt: skip
        for d in rows:
            entry = {"id": d["id"], "label": d["label"], "macs": list(d["macs"])}
            if d["id"] in dev:
                out[f"device:{d['id']}"] = {
                    "name": client_name(d["label"]),
                    "profile": dev[d["id"]],
                    "label": d["label"],
                    "devices": [entry],
                }
            elif d.get("group_id") in grp:
                out[f"group:{d['group_id']}"]["devices"].append(entry)
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
            if not desired and not tracked:
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
            self.conflicts, self.error = conflicts, None
            self.last_sync_ms = int(time.time() * 1000)
            return {"changes": changes, "conflicts": conflicts}

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
            due = self.dirty or now - last_full >= FULL_SYNC_S
            if not due or not self.actions.enabled() or not self.actions.service.configured:
                continue
            try:
                if await asyncio.to_thread(self.has_work):
                    result = await self.reconcile()
                    last_full = now
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
