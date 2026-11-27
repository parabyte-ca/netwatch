"""Network assets on the map: unmanaged switches, and what is pinned behind them.

Map only. An unmanaged switch cannot be seen on the network, so you add it and pin devices behind
it by hand; NetWatch only suggests where one probably is. The diagnosis never reads any of this.
"""

from __future__ import annotations

import asyncio
import re
import time

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.assets import AssetError, hints
from ..services.discovery.inventory import device_label
from .auth import security_event

router = APIRouter(prefix="/api/assets")


class AssetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    uplink: str = Field(default="hub", max_length=80)
    notes: str | None = Field(default=None, max_length=200)


class AssetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=40)
    uplink: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=200)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[str] = Field(min_length=1, max_length=200)
    parent: str | None = Field(default=None, max_length=80)  # asset:<id>, deco:<mac> or null


def _st(request: Request):
    return request.app.state


def _fail(exc: AssetError, code: int = status.HTTP_422_UNPROCESSABLE_ENTITY):
    return HTTPException(code, str(exc))


def _decos(request: Request) -> dict[str, dict]:
    deco = getattr(_st(request), "deco", None)
    return dict(deco.decos) if deco and deco.decos else {}


def _check_deco(request: Request, ref: str | None) -> None:
    """A Deco uplink or pin must name a Deco NetWatch knows (``deco:<mac>``)."""
    if ref and ref.startswith("deco:") and ref[5:] not in _decos(request):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "NetWatch does not know that Deco"
        )


async def _state(request: Request) -> dict:
    st = _st(request)
    items = await asyncio.to_thread(st.assets.list)
    pins = await asyncio.to_thread(st.assets.links)
    rows = await asyncio.to_thread(st.discovery.inventory.listing, int(time.time() * 1000))
    by_id = {f"dev:{d['id']}": d["label"] for d in rows}
    for a in items:
        a["members"] = [
            {
                "key": k,
                "label": "TrueNAS (NetWatch)"
                if k == "truenas"
                else by_id.get(k, "a removed device"),
            }
            for k in a["members"]
        ]
    decos = _decos(request)
    return {
        "assets": items,
        "decos": [
            {"id": f"deco:{mac}", "name": f"Deco {d.get('name') or mac}"}
            for mac, d in sorted(decos.items(), key=lambda kv: str(kv[1].get("name")))
        ],
        "pins": pins,
        "hints": hints(rows, decos, pins),
    }


@router.get("")
async def list_assets(request: Request) -> dict:
    return await _state(request)


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_asset(body: AssetIn, request: Request) -> dict:
    _check_deco(request, body.uplink)
    try:
        a = await asyncio.to_thread(_st(request).assets.create, body.name, body.uplink, body.notes)
    except AssetError as exc:
        raise _fail(exc) from None
    await security_event(
        request,
        "info",
        f"Map: added the switch {a['name']}",
        {"action": "asset_add", "id": a["id"]},
    )
    return {"asset": a, **await _state(request)}


@router.put("/{asset_id}")
async def change_asset(asset_id: int, body: AssetPatch, request: Request) -> dict:
    values = body.model_dump(exclude_unset=True)
    if values.get("uplink"):
        _check_deco(request, values["uplink"])
    store = _st(request).assets
    before = await asyncio.to_thread(store.get, asset_id)
    if before is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such switch")
    try:
        a = await asyncio.to_thread(store.update, asset_id, values)
    except AssetError as exc:
        raise _fail(exc) from None
    changed = [k for k in ("name", "uplink", "notes") if before[k] != a[k]]
    if changed:
        await security_event(
            request,
            "info",
            f"Map: changed the switch {a['name']} ({', '.join(changed)})",
            {"action": "asset_change", "id": asset_id, "fields": changed},
        )
    return {"asset": a, **await _state(request)}


@router.delete("/{asset_id}")
async def remove_asset(asset_id: int, request: Request) -> dict:
    try:
        a = await asyncio.to_thread(_st(request).assets.delete, asset_id)
    except AssetError as exc:
        raise _fail(exc, status.HTTP_404_NOT_FOUND) from None
    await security_event(
        request,
        "info",
        f"Map: removed the switch {a['name']} (what was behind it moved to its uplink)",
        {"action": "asset_remove", "id": asset_id},
    )
    return await _state(request)


@router.post("/assign")
async def assign(body: AssignIn, request: Request) -> dict:
    """Pin devices (or the NetWatch host) behind a switch, a Deco or the Hub, or unpin them.

    A device last reported on Wi-Fi is still pinned (the report can be stale, for example after a
    network card swap); the reply carries a ``warnings`` line for it.
    """
    st = _st(request)
    _check_deco(request, body.parent)
    keys: list[str] = []
    warnings: list[str] = []
    for k in body.nodes:
        m = re.fullmatch(r"dev:(\d{1,9})", k)
        if not m:
            if k != "truenas":
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "pin devices or TrueNAS only"
                )
            keys.append(k)
            continue
        dev = await asyncio.to_thread(st.device_store.get, int(m.group(1)))
        if dev is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such device")
        prim = (await asyncio.to_thread(st.groups.primaries, [dev["id"]]))[0]
        if body.parent and body.parent.startswith("asset:"):
            conn = (dev.get("details") or {}).get("connection")
            if conn and conn != "wired":  # advice only: the report can be stale after a card swap
                warnings.append(f"{device_label(dev)} was last reported on Wi-Fi ({conn})")
        keys.append(f"dev:{prim}")
    try:
        changed = await asyncio.to_thread(st.assets.assign, keys, body.parent)
    except AssetError as exc:
        raise _fail(exc) from None
    if changed:
        target = "unpinned" if body.parent is None else f"pinned behind {body.parent}"
        await security_event(
            request,
            "info",
            f"Map: {changed} device{'s' if changed != 1 else ''} {target}",
            {"action": "asset_assign", "count": changed, "parent": body.parent},
        )
    return {**await _state(request), "warnings": warnings}
