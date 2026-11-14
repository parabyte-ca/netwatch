"""Device groups and tags: manage groups, assign devices (one or many), suggestions, export."""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.device_groups import COLOURS, ICONS, GroupError
from .auth import security_event

router = APIRouter(prefix="/api")

Colour = Literal[COLOURS]  # type: ignore[valid-type]
Icon = Literal[ICONS]  # type: ignore[valid-type]


def _groups(request: Request):
    return request.app.state.groups


async def _run(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except GroupError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


class GroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    colour: Colour
    icon: Icon
    quiet: bool = False


class GroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=40)
    colour: Colour | None = None
    icon: Icon | None = None
    sort: int | None = Field(default=None, ge=0, le=10_000)
    quiet: bool | None = None


class BulkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] = Field(min_length=1, max_length=500)
    group_id: int | None = Field(default=None, ge=0)  # 0 = remove from its group
    add_tags: list[str] = Field(default=[], max_length=12)
    remove_tags: list[str] = Field(default=[], max_length=12)


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_name: str = Field(min_length=1, max_length=40)
    device_ids: list[int] = Field(min_length=1, max_length=500)


@router.get("/device-groups")
async def list_groups(request: Request) -> dict:
    g = _groups(request)
    return {
        "groups": await asyncio.to_thread(g.list),
        "colours": list(COLOURS),
        "icons": list(ICONS),
    }


@router.post("/device-groups", status_code=status.HTTP_201_CREATED)
async def create_group(body: GroupIn, request: Request) -> dict:
    g = await _run(_groups(request).create, body.name, body.colour, body.icon, body.quiet)
    await security_event(request, "info", f"Device group created: {g['name']}",
                         {"action": "group_created", "group_id": g["id"]})  # fmt: skip
    return g


@router.patch("/device-groups/{group_id}")
async def edit_group(group_id: int, body: GroupPatch, request: Request) -> dict:
    gs = _groups(request)
    return await _run(lambda: gs.update(group_id, **body.model_dump(exclude_unset=True)))


@router.delete("/device-groups/{group_id}")
async def delete_group(group_id: int, request: Request) -> dict:
    gs = _groups(request)
    g = await asyncio.to_thread(gs.get, group_id)
    if g is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such group")
    n = await _run(gs.delete, group_id)
    plural = "s" if n != 1 else ""
    title = f"Device group deleted: {g['name']} ({n} device{plural} ungrouped)"
    await security_event(request, "info", title, {"action": "group_deleted", "group_id": group_id})
    return {"ungrouped": n}


@router.get("/device-groups/stats")
async def group_stats(request: Request, hours: int = 24) -> list[dict]:
    if hours not in (24, 168):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "hours must be 24 or 168")
    end = int(time.time() * 1000)
    return await asyncio.to_thread(request.app.state.group_stats, end - hours * 3_600_000, end)


@router.get("/device-groups/suggestions")
async def suggestions(request: Request) -> list[dict]:
    return await asyncio.to_thread(_groups(request).suggestions)


@router.post("/device-groups/suggestions/accept")
async def accept_suggestion(body: AcceptIn, request: Request) -> dict:
    return await _run(_groups(request).accept, body.group_name, body.device_ids)


@router.get("/device-tags")
async def list_tags(request: Request) -> list[dict]:
    return await asyncio.to_thread(_groups(request).tags)


@router.post("/devices/bulk")
async def bulk(body: BulkIn, request: Request) -> dict:
    gs = _groups(request)

    def run() -> None:
        if "group_id" in body.model_fields_set:
            gs.assign(body.ids, body.group_id or None)
        if body.add_tags:
            gs.add_tags(body.ids, body.add_tags)
        if body.remove_tags:
            gs.remove_tags(body.ids, body.remove_tags)

    await _run(run)
    request.app.state.discovery.inventory.forget()
    return {"updated": len(set(body.ids))}


@router.get("/device-groups/export")
async def export_groups(request: Request) -> Response:
    import json

    data = await asyncio.to_thread(_groups(request).export)
    name = time.strftime("netwatch-groups-%Y%m%d.json")
    return Response(json.dumps(data, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


@router.post("/device-groups/import")
async def import_groups(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "not JSON") from None
    r = await _run(_groups(request).import_, data)
    title = f"Device groups imported: {r['groups_created']} new groups, {r['devices']} devices"
    await security_event(request, "info", title, {"action": "groups_imported"})
    return r
