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
from ..services.adguard.profiles import PROFILE_LABEL
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


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Literal["default", "no_blocking", "strict"]


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
            "label": d["label"],
            "devices": [
                {"id": x["id"], "label": x["label"]}
                for x in d["devices"]
                if x["id"] not in conflict_ids
            ],
        }
        for key, d in sorted(desired.items(), key=lambda kv: kv[1]["name"].lower())
    ]
    return {**p.state(), "managed": clients}


async def _set(request: Request, kind: str, target_id: int, body: ProfileIn) -> dict:
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
    label = PROFILE_LABEL[body.profile]
    r = await _run(request, f"set {what} to {label}", setter(target_id, body.profile))
    changes = ", ".join(f"{c['action']} {c['client']}" for c in r["changes"]) or "no change needed"
    await security_event(
        request,
        "info",
        f"AdGuard Home: {what} set to {label} ({changes})",
        {"action": "adguard_profile", "kind": kind, "id": target_id, "profile": body.profile},
    )
    return {**r, "state": p.state()}


@router.put("/profiles/device/{device_id}")
async def set_device_profile(device_id: int, body: ProfileIn, request: Request) -> dict:
    return await _set(request, "device", device_id, body)


@router.put("/profiles/group/{group_id}")
async def set_group_profile(group_id: int, body: ProfileIn, request: Request) -> dict:
    return await _set(request, "group", group_id, body)
