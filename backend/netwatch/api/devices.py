"""Device inventory and discovery controls."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.discovery.classify import TYPES
from ..services.discovery.inventory import device_label, summarise_group
from ..services.events import ANOMALY, DEVICE

router = APIRouter(prefix="/api")

DeviceType = Literal[TYPES]  # type: ignore[valid-type]
EVENT_LOOKBACK_MS = 30 * 86_400_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _state(request: Request):
    return request.app.state


async def _primary(request: Request, device_id: int) -> tuple[dict, list[dict]]:
    """The device (resolved to its primary if it was merged) and every member row."""
    store = _state(request).device_store
    d = await asyncio.to_thread(store.get, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    if d["merged_into"]:
        p = await asyncio.to_thread(store.get, d["merged_into"])
        d = p or d
    members = [d, *await asyncio.to_thread(store.members, d["id"])]
    return d, members


@router.get("/devices")
async def list_devices(request: Request, q: str | None = None) -> dict:
    now = _now_ms()
    q = (q or "").strip()[:64] or None
    rows = await asyncio.to_thread(_state(request).discovery.inventory.listing, now, q=q)
    return {"now_ms": now, "devices": rows}


@router.get("/devices/{device_id}")
async def device_detail(device_id: int, request: Request) -> dict:
    st = _state(request)
    d, members = await _primary(request, device_id)
    now = _now_ms()
    ips = await asyncio.to_thread(st.device_store.ips, [m["id"] for m in members])
    target = (
        await asyncio.to_thread(st.store.get_target, d["target_id"]) if d["target_id"] else None
    )
    macs = {m["mac"] for m in members}
    recent = await asyncio.to_thread(
        st.events.list, now - EVENT_LOOKBACK_MS, now + 1, kinds=[ANOMALY, DEVICE], limit=2000
    )
    related = [
        e
        for e in recent
        if e["details"].get("device_id") in {m["id"] for m in members}
        or any(mac in json.dumps(e["details"]) for mac in macs)
    ][:100]
    return {
        "device": summarise_group(d, members, now),
        "members": members,
        "ips": ips,
        "target": target,
        "events": related,
    }


class DeviceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, max_length=64)
    type_override: DeviceType | None = None
    notes: str | None = Field(default=None, max_length=500)


@router.patch("/devices/{device_id}")
async def edit_device(device_id: int, body: DeviceEdit, request: Request) -> dict:
    st = _state(request)
    d, _ = await _primary(request, device_id)
    values = {k: (v or None) for k, v in body.model_dump(exclude_unset=True).items()}
    if values:
        await asyncio.to_thread(st.device_store.update, d["id"], values)
        st.discovery.inventory.forget(d["mac"])
        if "name" in values and d["target_id"] and values["name"]:
            await asyncio.to_thread(
                st.store.update_target, d["target_id"], {"name": values["name"]}
            )
    return await device_detail(d["id"], request)


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    into: int


@router.post("/devices/{device_id}/merge")
async def merge_device(device_id: int, body: MergeIn, request: Request) -> dict:
    """Treat this device as the same physical device as `into` (e.g. a phone's new private MAC)."""
    st = _state(request)
    src, src_members = await _primary(request, device_id)
    dst, _ = await _primary(request, body.into)
    if src["id"] == dst["id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "already the same device")
    if src["target_id"] and dst["target_id"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "both devices are monitored; stop monitoring one first"
        )
    for m in src_members:
        await asyncio.to_thread(st.device_store.update, m["id"], {"merged_into": dst["id"]})
    if src["target_id"]:
        await asyncio.to_thread(st.device_store.update, src["id"], {"target_id": None})
        await asyncio.to_thread(st.device_store.update, dst["id"], {"target_id": src["target_id"]})
    st.discovery.inventory.forget()
    return await device_detail(dst["id"], request)


@router.post("/devices/{device_id}/unmerge")
async def unmerge_device(device_id: int, request: Request) -> dict:
    st = _state(request)
    d = await asyncio.to_thread(st.device_store.get, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    if not d["merged_into"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "device is not merged")
    await asyncio.to_thread(st.device_store.update, device_id, {"merged_into": None})
    st.discovery.inventory.forget(d["mac"])
    return await device_detail(device_id, request)


@router.post("/devices/{device_id}/monitor", status_code=status.HTTP_201_CREATED)
async def monitor_device(device_id: int, request: Request) -> dict:
    """Create a ping target for the device; it follows the device if its address changes."""
    st = _state(request)
    d, members = await _primary(request, device_id)
    if d["target_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "device is already monitored")
    summary = summarise_group(d, members, _now_ms())
    ip = summary["last_ip"]
    if not ip:
        raise HTTPException(status.HTTP_409_CONFLICT, "no known IP address for this device yet")
    if await asyncio.to_thread(st.store.count_targets) >= st.settings.max_targets:
        raise HTTPException(status.HTTP_409_CONFLICT, "target limit reached")
    target = await asyncio.to_thread(
        st.store.create_target,
        {
            "name": device_label(d)[:64],
            "kind": "icmp",
            "host": ip,
            "interval_s": 5.0,
            "timeout_s": 2.0,
            "enabled": True,
            "group": "device",
            "auto": False,
        },
    )
    await asyncio.to_thread(st.device_store.update, d["id"], {"target_id": target["id"]})
    st.discovery.inventory.forget(d["mac"])
    await st.monitor.reload()
    return await device_detail(d["id"], request)


@router.delete("/devices/{device_id}/monitor")
async def unmonitor_device(device_id: int, request: Request) -> dict:
    st = _state(request)
    d, _ = await _primary(request, device_id)
    if not d["target_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "device is not monitored")
    await asyncio.to_thread(st.store.delete_target, d["target_id"])
    await asyncio.to_thread(st.device_store.update, d["id"], {"target_id": None})
    await st.monitor.reload()
    return await device_detail(d["id"], request)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_device(device_id: int, request: Request) -> Response:
    """Remove the device (it reappears if it is seen again)."""
    st = _state(request)
    d = await asyncio.to_thread(st.device_store.get, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    if d["target_id"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "stop monitoring the device first")
    await asyncio.to_thread(st.device_store.delete, device_id)
    st.discovery.inventory.forget()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── discovery controls ────────────────────────────────────────────────────────


@router.get("/discovery")
async def discovery_status(request: Request) -> dict:
    st = _state(request)
    body = await asyncio.to_thread(st.discovery.status)
    rows = await asyncio.to_thread(st.discovery.inventory.listing, _now_ms())
    body["devices"] = {"total": len(rows), "online": sum(1 for r in rows if r["online"])}
    return body


class DiscoverySettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


@router.put("/discovery")
async def set_discovery(body: DiscoverySettingsIn, request: Request) -> dict:
    st = _state(request)
    await asyncio.to_thread(st.discovery.set_active, body.active)
    return await discovery_status(request)


@router.post("/discovery/scan")
async def scan_now(request: Request) -> dict:
    """Run one active scan now (an explicit request, so allowed even if scheduled scans are off)."""
    d = _state(request).discovery
    if d.scanning:
        raise HTTPException(status.HTTP_409_CONFLICT, "a scan is already running")
    reason = d.scan_possible()
    if reason:
        raise HTTPException(status.HTTP_409_CONFLICT, reason)
    return await d.scan(trigger="manual")


@router.post("/discovery/dns-check")
async def dns_check_now(request: Request) -> dict:
    return await _state(request).discovery.check_dns()
