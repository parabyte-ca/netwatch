"""How to reach a device: its SSH link and admin web page, from the scan and what you save.

NetWatch never logs in; it hands out ssh:// and http(s):// links for your own apps. A re-check
probes only the device's own known LAN address, a few ports, and at most once per 10 seconds.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.discovery.inventory import device_label
from ..services.host_access import CHECK_PORTS, AccessError, hosts_of, view
from .auth import security_event
from .devices import _primary

router = APIRouter(prefix="/api/devices")

CHECK_EVERY_S = 10


class AccessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ssh_user: str | None = Field(default=None, max_length=32)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_mode: Literal["auto", "on", "off"] | None = None
    admin_url: str | None = Field(default=None, max_length=200)


async def _view(request: Request, device: dict) -> dict:
    st = request.app.state
    row = await asyncio.to_thread(st.access.get, device_id=device["id"])
    services = await asyncio.to_thread(st.exposure_store.services, device["id"])
    return view(row, services, device.get("last_ip"))


@router.get("/{device_id}/access")
async def get_access(device_id: int, request: Request) -> dict:
    d, _members = await _primary(request, device_id)
    return await _view(request, d)


@router.put("/{device_id}/access")
async def put_access(device_id: int, body: AccessIn, request: Request) -> dict:
    d, _members = await _primary(request, device_id)
    changes = body.model_dump(exclude_unset=True)
    hosts = hosts_of(d.get("last_ip"), d.get("hostname"))
    before = await asyncio.to_thread(request.app.state.access.get, device_id=d["id"]) or {}
    defaults = {"ssh_port": 22, "ssh_mode": "auto"}

    def changed(k: str, v) -> bool:
        return (before.get(k, defaults.get(k)) or None) != (
            (v.strip() if isinstance(v, str) else v) or None
        )

    try:
        await asyncio.to_thread(
            lambda: request.app.state.access.save(device_id=d["id"], hosts=hosts, **changes)
        )
    except AccessError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    what = sorted(k for k, v in changes.items() if changed(k, v))
    if what:
        await security_event(
            request, "info",
            f"Access: changed {', '.join(what)} for {device_label(d)}",
            {"action": "access_changed", "device_id": d["id"]},
        )  # fmt: skip
    return await _view(request, d)


@router.post("/{device_id}/access/check")
async def check_access(device_id: int, request: Request) -> dict:
    d, _members = await _primary(request, device_id)
    st = request.app.state
    ip = d.get("last_ip")
    if not ip:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "this device has no address yet")
    last: dict[int, float] = st.access_checks
    now = time.monotonic()
    if now - last.get(d["id"], -CHECK_EVERY_S) < CHECK_EVERY_S:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "checked a moment ago; wait 10 s")
    last[d["id"]] = now
    try:
        await st.exposure.check_device(d["id"], ip, list(CHECK_PORTS))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return await _view(request, d)
