"""Opt-in AdGuard Home actions (Admin → settings → "AdGuard Home actions").

Every change needs a signed-in session (and the X-Requested-With header, like every write), is
confirmed in the UI first, and is written to the security log, failures included.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.adguard.actions import ActionError, ActionsDisabled
from ..services.adguard.profiles import describe
from ..services.adguard.schedules import describe_effects
from ..services.discovery.inventory import device_label
from .auth import security_event

router = APIRouter(prefix="/api/adguard/actions")


class PauseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(ge=1, le=1440)


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=1, max_length=253)
    kind: Literal["allow", "block"]


class RuleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str = Field(min_length=1, max_length=300)


class UpstreamsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upstreams: list[str] = Field(min_length=1, max_length=5)


def _acts(request: Request):
    return request.app.state.adguard_actions


def _who(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", None) or "admin"


def _clock(request: Request, ms: int) -> str:
    try:
        tz = ZoneInfo(request.app.state.settings.tz)
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        tz = None
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%H:%M")


async def _run(request: Request, what: str, coro):
    """Run an action; log success or failure to the security log; map errors to HTTP."""
    try:
        result = await coro
    except ActionsDisabled as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ActionError as exc:
        await security_event(request, "warning", f"AdGuard Home: could not {what}: {exc}",
                             {"action": "adguard", "ok": False})  # fmt: skip
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return result


@router.get("")
async def actions_state(request: Request) -> dict:
    acts = _acts(request)
    state = await asyncio.to_thread(acts.state)
    state["upstreams"] = None
    if state["enabled"]:
        # Not set up or unreachable: the page says so from the status.
        with contextlib.suppress(ActionError, ActionsDisabled):
            state["upstreams"] = await acts.current_upstreams()
    return state


@router.post("/pause")
async def pause(body: PauseIn, request: Request) -> dict:
    p = await _run(request, "pause protection", _acts(request).pause(body.minutes, _who(request)))
    await security_event(
        request, "warning",
        f"AdGuard Home: protection paused for {body.minutes} min (until "
        f"{_clock(request, p['until_ms'])})",
        {"action": "adguard_pause", "minutes": body.minutes},
    )  # fmt: skip
    return {"pause": p}


@router.post("/resume")
async def resume(request: Request) -> dict:
    await _run(request, "resume protection", _acts(request).resume(_who(request)))
    await security_event(request, "info", "AdGuard Home: protection turned back on",
                         {"action": "adguard_resume"})  # fmt: skip
    return {"pause": None}


@router.post("/rules")
async def add_rule(body: RuleIn, request: Request) -> dict:
    entry = await _run(
        request, f"{body.kind} {body.domain[:80]}",
        _acts(request).add_rule(body.domain, body.kind, _who(request)),
    )  # fmt: skip
    verb = "allowed" if body.kind == "allow" else "blocked"
    await security_event(request, "info", f"AdGuard Home: {verb} {entry['domain']}",
                         {"action": "adguard_rule", "rule": entry["rule"]})  # fmt: skip
    return {"rule": entry, "rules": _acts(request).rules()}


@router.delete("/rules")
async def remove_rule(body: RuleOut, request: Request) -> dict:
    entry = await _run(request, "remove a rule", _acts(request).remove_rule(body.rule))
    await security_event(request, "info", f"AdGuard Home: removed rule {entry['rule']}",
                         {"action": "adguard_rule_removed", "rule": entry["rule"]})  # fmt: skip
    return {"rules": _acts(request).rules()}


@router.post("/upstreams/test")
async def test_upstreams(body: UpstreamsIn, request: Request) -> dict:
    try:
        return await _acts(request).test_upstreams(body.upstreams)
    except ActionsDisabled as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ActionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


@router.post("/upstreams")
async def switch_upstreams(body: UpstreamsIn, request: Request) -> dict:
    r = await _run(request, "switch the upstream DNS",
                   _acts(request).switch_upstreams(body.upstreams))  # fmt: skip
    await _log_switch(request, r)
    return r


@router.post("/upstreams/restore")
async def restore_upstreams(request: Request) -> dict:
    r = await _run(request, "switch back to the previous upstream DNS",
                   _acts(request).restore_upstreams())  # fmt: skip
    await _log_switch(request, r)
    return r


async def _log_switch(request: Request, r: dict) -> None:
    await security_event(
        request, "warning",
        f"AdGuard Home: upstream DNS switched from {', '.join(r['previous'])} to "
        f"{', '.join(r['current'])}",
        {"action": "adguard_upstreams", "previous": r["previous"], "current": r["current"]},
    )  # fmt: skip


# ── DHCP reservations ────────────────────────────────────────────────────────


class ReservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mac: str = Field(min_length=12, max_length=17)
    ip: str = Field(min_length=7, max_length=15)
    hostname: str | None = Field(default=None, max_length=63)


class ReservationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mac: str = Field(min_length=12, max_length=17)


class BulkReserveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_ids: list[int] = Field(min_length=1, max_length=100)


def _devices_by_mac(request: Request) -> dict[str, dict]:
    now = int(time.time() * 1000)
    rows = request.app.state.discovery.inventory.listing(now)
    return {m: d for d in rows for m in d["macs"]}


def _who_is(by_mac: dict[str, dict], mac: str) -> str:
    d = by_mac.get(mac)
    return f"{d['label']} ({mac})" if d else mac


@router.get("/dhcp")
async def dhcp(request: Request) -> dict:
    d = await _run(request, "read DHCP", _acts(request).dhcp())
    by_mac = await asyncio.to_thread(_devices_by_mac, request)
    ours = set(d["ours"])

    def row(lease: dict) -> dict:
        dev = by_mac.get(lease["mac"])
        return {
            **lease,
            "by_netwatch": lease["mac"] in ours,
            "device": {"id": dev["id"], "label": dev["label"]} if dev else None,
        }

    return {
        "available": d["available"],
        "enabled": d["enabled"],
        "v4": d.get("v4"),
        "reservations": sorted(
            (row(x) for x in d["leases"] if x["static"]),
            key=lambda r: tuple(int(p) for p in r["ip"].split(".")),
        ),
        "leases": [row(x) for x in d["leases"] if not x["static"]],
    }


@router.post("/dhcp/reservations")
async def reserve(body: ReservationIn, request: Request) -> dict:
    r = await _run(
        request,
        f"reserve {body.ip[:15]}",
        _acts(request).reserve(body.mac, body.ip, body.hostname, _who(request)),
    )
    by_mac = await asyncio.to_thread(_devices_by_mac, request)
    what = (
        f"changed {r['changed_from']} to {r['ip']}" if r["changed_from"] else f"reserved {r['ip']}"
    )
    await security_event(
        request,
        "info",
        f"AdGuard Home: {what} for {_who_is(by_mac, r['mac'])}",
        {"action": "adguard_reservation", "mac": r["mac"], "ip": r["ip"]},
    )
    return {"reservation": r}


@router.delete("/dhcp/reservations")
async def unreserve(body: ReservationOut, request: Request) -> dict:
    r = await _run(
        request, "remove a reservation", _acts(request).unreserve(body.mac, _who(request))
    )
    by_mac = await asyncio.to_thread(_devices_by_mac, request)
    await security_event(
        request,
        "info",
        f"AdGuard Home: removed the reservation of {r['ip']} for {_who_is(by_mac, r['mac'])}",
        {"action": "adguard_reservation_removed", "mac": r["mac"], "ip": r["ip"]},
    )
    return {"removed": r}


@router.post("/dhcp/reservations/bulk")
async def reserve_current(body: BulkReserveIn, request: Request) -> dict:
    """Reserve each device's current address (from its DHCP lease) under its current MAC."""
    acts = _acts(request)
    d = await _run(request, "read DHCP", acts.dhcp())
    leases = {x["mac"]: x for x in d["leases"]}
    by_id = {
        dev["id"]: dev
        for dev in await asyncio.to_thread(
            request.app.state.discovery.inventory.listing, int(time.time() * 1000)
        )
    }
    results = []
    for device_id in dict.fromkeys(body.device_ids):
        dev = by_id.get(device_id)
        if dev is None:
            results.append({"id": device_id, "ok": False, "error": "unknown device"})
            continue
        lease = next(
            (
                leases[m]
                for m in dev["macs"]
                if m in leases and leases[m]["ip"] == dev.get("last_ip")
            ),
            None,
        )
        if lease is None:
            results.append(
                {
                    "id": device_id,
                    "label": dev["label"],
                    "ok": False,
                    "error": "no current DHCP lease from AdGuard Home",
                }
            )
            continue
        if lease["static"]:
            results.append(
                {
                    "id": device_id,
                    "label": dev["label"],
                    "ok": True,
                    "ip": lease["ip"],
                    "already": True,
                }
            )
            continue
        try:
            r = await acts.reserve(lease["mac"], lease["ip"], dev["label"], _who(request))
        except ActionsDisabled as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
        except ActionError as exc:
            results.append({"id": device_id, "label": dev["label"], "ok": False, "error": str(exc)})
            continue
        await security_event(
            request,
            "info",
            f"AdGuard Home: reserved {r['ip']} for {dev['label']} ({r['mac']})",
            {"action": "adguard_reservation", "mac": r["mac"], "ip": r["ip"]},
        )
        results.append({"id": device_id, "label": dev["label"], "ok": True, "ip": r["ip"]})
    return {"results": results}


# ── settings per device and per group ───────────────────────────────────────


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filtering: Literal["default", "no_blocking", "strict"] = "default"
    youtube: Literal["default", "restricted", "blocked", "allowed"] = "default"
    services: list[str] = Field(default_factory=list, max_length=30)


def _profiles(request: Request):
    return request.app.state.adguard_profiles


@router.get("/profiles")
async def profiles(request: Request) -> dict:
    """The choices, the clients NetWatch keeps for them (with their devices) and conflicts."""
    if not _acts(request).enabled():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AdGuard Home actions are turned off")
    p = _profiles(request)
    desired = await asyncio.to_thread(p.desired)
    conflict_ids = {c["device_id"] for c in p.conflicts}
    clients = [
        {
            "key": key,
            "name": d["name"],
            "profile": d["profile"],
            "allow": d.get("allow", []),
            "label": d["label"],
            "devices": [
                {"id": x["id"], "label": x["label"]}
                for x in d["devices"]
                if x["id"] not in conflict_ids
            ],
        }
        for key, d in sorted(desired.items(), key=lambda kv: kv[1]["name"].lower())
    ]
    services: list[dict] = []
    with contextlib.suppress(ActionError, ActionsDisabled):
        services = await p.services_all()
    return {**p.state(), "managed": clients, "services_all": services}


async def _set(request: Request, kind: str, target_id: int, body: SettingsIn) -> dict:
    p = _profiles(request)
    setter = p.set_device if kind == "device" else p.set_group
    if kind == "device":
        dev = await asyncio.to_thread(request.app.state.device_store.get, target_id)
        if dev is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such device")
        what = device_label(dev)[:60]
    else:
        g = await asyncio.to_thread(request.app.state.groups.get, target_id)
        if g is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such group")
        what = f"the group {g['name']}"
    settings = body.model_dump()
    names = {x["id"]: x["name"] for x in (p._services[1] if p._services else [])}
    label = describe(settings, names)
    r = await _run(request, f"set {what} to {label}", setter(target_id, settings))
    changes = ", ".join(f"{c['action']} {c['client']}" for c in r["changes"]) or "no change needed"
    await security_event(
        request,
        "info",
        f"AdGuard Home: {what} set to {label} ({changes})",
        {"action": "adguard_profile", "kind": kind, "id": target_id, "settings": settings},
    )
    return {**r, "state": p.state()}


@router.put("/profiles/device/{device_id}")
async def set_device_profile(device_id: int, body: SettingsIn, request: Request) -> dict:
    return await _set(request, "device", device_id, body)


@router.put("/profiles/group/{group_id}")
async def set_group_profile(group_id: int, body: SettingsIn, request: Request) -> dict:
    return await _set(request, "group", group_id, body)


class AllowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domains: list[str] = Field(default_factory=list, max_length=50)


async def _set_allow(request: Request, kind: str, target_id: int, body: AllowIn) -> dict:
    p = _profiles(request)
    if kind == "device":
        dev = await asyncio.to_thread(request.app.state.device_store.get, target_id)
        if dev is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such device")
        what = device_label(dev)[:60]
        key_id = (await asyncio.to_thread(request.app.state.groups.primaries, [target_id]))[0]
    else:
        g = await asyncio.to_thread(request.app.state.groups.get, target_id)
        if g is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such group")
        what, key_id = f"the group {g['name']}", target_id
    before = set((await asyncio.to_thread(p.allow_lists)).get(f"{kind}:{key_id}", []))
    r = await _run(request, f"change always-allowed domains for {what}",
                   p.set_allow(kind, target_id, body.domains))  # fmt: skip
    after = set((await asyncio.to_thread(p.allow_lists)).get(f"{kind}:{key_id}", []))
    for dom in sorted(after - before):
        await security_event(request, "info", f"AdGuard Home: always allowed {dom} for {what}",
                             {"action": "adguard_allow", "kind": kind, "id": target_id,
                              "domain": dom})  # fmt: skip
    for dom in sorted(before - after):
        await security_event(request, "info", f"AdGuard Home: {dom} no longer always allowed "
                             f"for {what}", {"action": "adguard_allow_removed", "kind": kind,
                                             "id": target_id, "domain": dom})  # fmt: skip
    return {**r, "allow": sorted(after), "state": p.state()}


@router.put("/profiles/device/{device_id}/allow")
async def set_device_allow(device_id: int, body: AllowIn, request: Request) -> dict:
    return await _set_allow(request, "device", device_id, body)


@router.put("/profiles/group/{group_id}/allow")
async def set_group_allow(group_id: int, body: AllowIn, request: Request) -> dict:
    return await _set_allow(request, "group", group_id, body)


# ── schedules ───────────────────────────────────────────────────────────────


class TargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["group", "device"]
    id: int


class EffectsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    internet_off: bool = False
    youtube_off: bool = False
    strict: bool = False
    services: list[str] = Field(default_factory=list, max_length=30)


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    target: TargetIn
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(min_length=1)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    effects: EffectsIn
    enabled: bool = True


class ExtendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    how: Literal["15", "60", "until_next", "skip"]


class NowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: TargetIn
    minutes: Literal[30, 60, 120]


def _sched(request: Request):
    return request.app.state.adguard_schedules


def _gate(request: Request) -> None:
    if not _acts(request).enabled():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AdGuard Home actions are turned off")


async def _target(request: Request, t: TargetIn) -> tuple[dict, str]:
    """The target as stored (a device by its primary row) and its name for the log."""
    st = request.app.state
    if t.kind == "group":
        g = await asyncio.to_thread(st.groups.get, t.id)
        if g is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such group")
        return {"kind": "group", "id": t.id}, f"the group {g['name']}"
    dev = await asyncio.to_thread(st.device_store.get, t.id)
    if dev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such device")
    prim = (await asyncio.to_thread(st.groups.primaries, [t.id]))[0]
    return {"kind": "device", "id": prim}, device_label(dev)[:60]


async def _apply(request: Request, what: str) -> dict:
    """Bring AdGuard Home in line now, so a change shows at once rather than within 15 s."""
    return await _run(request, what, _profiles(request).reconcile())


async def _sched_state(request: Request) -> dict:
    s = await asyncio.to_thread(_sched(request).state)
    labels = {f"group:{g['id']}": g["name"] for g in request.app.state.groups.list()}
    for row in [*s["schedules"], *s["active"]]:
        k = f"{row['target']['kind']}:{row['target']['id']}"
        if k not in labels and row["target"]["kind"] == "device":
            dev = await asyncio.to_thread(request.app.state.device_store.get, row["target"]["id"])
            labels[k] = device_label(dev) if dev else "a removed device"
        row["target_label"] = labels.get(k, "a removed group")
    return s


@router.get("/schedules")
async def schedules(request: Request) -> dict:
    _gate(request)
    return await _sched_state(request)


@router.post("/schedules")
async def add_schedule(body: ScheduleIn, request: Request) -> dict:
    _gate(request)
    target, who = await _target(request, body.target)
    raw = {**body.model_dump(), "target": target}
    await _run(request, f"add a schedule for {who}", _profiles(request)._check_apps(
        {"services": raw["effects"]["services"]}))  # fmt: skip
    try:
        s = await asyncio.to_thread(_sched(request).add, raw, _who(request))
    except ActionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    await _apply(request, f"apply the schedule {s['name']}")
    await security_event(
        request, "info",
        f"AdGuard Home: schedule \"{s['name']}\" added for {who} ({', '.join(s['days'])} "
        f"{s['start']} to {s['end']}: {describe_effects(s['effects'])})",
        {"action": "adguard_schedule", "schedule": s["id"]},
    )  # fmt: skip
    return {"schedule": s, "state": await _sched_state(request)}


@router.put("/schedules/{sid}")
async def update_schedule(sid: str, body: ScheduleIn, request: Request) -> dict:
    _gate(request)
    target, who = await _target(request, body.target)
    raw = {**body.model_dump(), "target": target}
    await _run(request, "change a schedule", _profiles(request)._check_apps(
        {"services": raw["effects"]["services"]}))  # fmt: skip
    try:
        s = await asyncio.to_thread(_sched(request).update, sid, raw)
    except ActionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    await _apply(request, f"apply the schedule {s['name']}")
    on = "" if s["enabled"] else " (turned off)"
    await security_event(request, "info",
                         f"AdGuard Home: schedule \"{s['name']}\" for {who} changed{on}",
                         {"action": "adguard_schedule", "schedule": sid})  # fmt: skip
    return {"schedule": s, "state": await _sched_state(request)}


@router.delete("/schedules/{sid}")
async def delete_schedule(sid: str, request: Request) -> dict:
    _gate(request)
    try:
        s = await asyncio.to_thread(_sched(request).delete, sid)
    except ActionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    await _apply(request, "remove a schedule")
    await security_event(request, "info", f"AdGuard Home: schedule \"{s['name']}\" deleted",
                         {"action": "adguard_schedule", "schedule": sid})  # fmt: skip
    return {"state": await _sched_state(request)}


_HOW = {"15": "15 more minutes", "60": "1 more hour", "until_next": "off until it next starts",
        "skip": "skipped once"}  # fmt: skip


@router.post("/schedules/{sid}/extend")
async def extend_schedule(sid: str, body: ExtendIn, request: Request) -> dict:
    _gate(request)
    s = await asyncio.to_thread(_sched(request).get, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such schedule")
    try:
        until = await asyncio.to_thread(_sched(request).extend, sid, body.how)
    except ActionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    await _apply(request, "apply an exception")
    await security_event(
        request, "info",
        f"AdGuard Home: schedule \"{s['name']}\": {_HOW[body.how]} "
        f"(until {_clock(request, until)})",
        {"action": "adguard_schedule_exception", "schedule": sid, "until_ms": until},
    )  # fmt: skip
    return {"off_until_ms": until, "state": await _sched_state(request)}


@router.delete("/schedules/{sid}/exception")
async def resume_schedule(sid: str, request: Request) -> dict:
    _gate(request)
    s = await asyncio.to_thread(_sched(request).get, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such schedule")
    await asyncio.to_thread(_sched(request).clear_exception, sid)
    await _apply(request, "resume a schedule")
    await security_event(request, "info", f"AdGuard Home: schedule \"{s['name']}\" back on",
                         {"action": "adguard_schedule_exception", "schedule": sid})  # fmt: skip
    return {"state": await _sched_state(request)}


@router.post("/schedules/now")
async def internet_off_now(body: NowIn, request: Request) -> dict:
    _gate(request)
    target, who = await _target(request, body.target)
    m = await asyncio.to_thread(_sched(request).manual_on, target, body.minutes, _who(request))
    await _apply(request, "turn the internet off now")
    await security_event(
        request, "warning",
        f"AdGuard Home: internet off now for {who}, {body.minutes} min "
        f"(until {_clock(request, m['until_ms'])})",
        {"action": "adguard_internet_off_now", "until_ms": m["until_ms"]},
    )  # fmt: skip
    return {"manual": m, "state": await _sched_state(request)}


@router.delete("/schedules/now/{mid}")
async def internet_back_on(mid: str, request: Request) -> dict:
    _gate(request)
    try:
        await asyncio.to_thread(_sched(request).manual_off, mid)
    except ActionError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    await _apply(request, "turn the internet back on")
    await security_event(request, "info", "AdGuard Home: internet back on (ended early)",
                         {"action": "adguard_internet_off_now"})  # fmt: skip
    return {"state": await _sched_state(request)}
