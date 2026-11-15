"""AdGuard Home settings per device and per group, kept as persistent clients NetWatch owns.

A device or a group gets a profile (default, no_blocking or strict). For each one that is not
default, NetWatch keeps a persistent client named "NetWatch: <name>" holding the devices' MACs.
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

PROFILES_KEY = "adguard_profiles"  # {"devices": {id: profile}, "groups": {id: profile}}
TRACKED_KEY = "adguard_profile_clients"  # {"device:5" | "group:3": client name}
PROFILES = ("default", "no_blocking", "strict")
PROFILE_LABEL = {"default": "Default", "no_blocking": "No blocking", "strict": "Strict"}
PREFIX = "NetWatch: "
SYNC_S = 15  # how soon a group change reaches AdGuard Home
FULL_SYNC_S = 300  # also re-check everything this often (drift)
_ENGINES = ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex", "youtube")


def client_body(name: str, profile: str, macs: list[str]) -> dict:
    """The persistent client AdGuard Home should have for this profile."""
    strict = profile == "strict"
    return {
        "name": name,
        "ids": sorted(macs),
        "use_global_settings": False,
        "filtering_enabled": strict,
        "parental_enabled": strict,
        "safebrowsing_enabled": strict,
        "safesearch": {"enabled": strict, **{e: strict for e in _ENGINES}},
        "safesearch_enabled": strict,  # AdGuard Home before v0.107.28
        "use_global_blocked_services": strict,
        "blocked_services": [],
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
    return bool(on) != want["safesearch"]["enabled"]


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

    # ── stored choices ───────────────────────────────────────────────────────

    def profiles(self) -> dict:
        p = self.store.kv_get(PROFILES_KEY, None) or {}
        return {
            "devices": {str(k): v for k, v in (p.get("devices") or {}).items() if v in PROFILES},
            "groups": {str(k): v for k, v in (p.get("groups") or {}).items() if v in PROFILES},
        }

    def tracked(self) -> dict[str, str]:
        t = self.store.kv_get(TRACKED_KEY, None) or {}
        return {str(k): str(v) for k, v in t.items()} if isinstance(t, dict) else {}

    def _save(self, kind: str, key: int, profile: str) -> None:
        if profile not in PROFILES:
            raise ActionError("profile must be default, no_blocking or strict")
        p = self.profiles()
        if profile == "default":
            p[kind].pop(str(key), None)
        else:
            p[kind][str(key)] = profile
        self.store.kv_set(PROFILES_KEY, p)

    def has_work(self) -> bool:
        p = self.profiles()
        return bool(p["devices"] or p["groups"] or self.tracked())

    # ── what AdGuard Home should hold ────────────────────────────────────────

    def desired(self) -> dict[str, dict]:
        """key → {name, profile, label, devices: [{id, label, macs}]}; prunes stale choices."""
        p = self.profiles()
        rows = self.inventory.listing(int(time.time() * 1000))
        ids = {d["id"] for d in rows}
        groups = {g["id"]: g for g in self.groups.list()}
        dev = {int(k): v for k, v in p["devices"].items() if int(k) in ids and v != "default"}
        grp = {int(k): v for k, v in p["groups"].items() if int(k) in groups and v != "default"}
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
                    want[key] = client_body(d["name"], d["profile"], macs)

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

    async def set_device(self, device_id: int, profile: str) -> dict:
        prim = (await asyncio.to_thread(self.groups.primaries, [device_id]))[0]
        await asyncio.to_thread(self._save, "devices", prim, profile)
        return await self.reconcile()

    async def set_group(self, group_id: int, profile: str) -> dict:
        if await asyncio.to_thread(self.groups.get, group_id) is None:
            raise ActionError("no such group")
        await asyncio.to_thread(self._save, "groups", group_id, profile)
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
