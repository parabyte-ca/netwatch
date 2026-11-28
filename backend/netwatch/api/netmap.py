"""Visual network map: the graph (live or at a past moment), incident highlight, saved layout."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.diagnosis import MERGE_GAP_MS

router = APIRouter(prefix="/api/map")

LAYOUT_KEY = "map_layout"
VIEWS_KEY = "map_views"
MAX_VIEWS = 20
MAX_NODES = 1000
MAX_COORD = 100_000


def _incident(st, incident: str | None, event_id: int | None, now: int):
    if event_id is not None:
        return st.diagnoser.incident_for_event(event_id, now)
    if incident is None:
        return None
    try:
        start = int(incident.removeprefix("i"))
    except ValueError:
        return None
    # Failures merged into the incident later start after it: widen the window until the
    # incident found no longer grows, so it matches what the Diagnosis page shows.
    window_end = start + 1
    for _ in range(20):
        match = next((i for i in st.diagnoser.incidents(start - 1, window_end, now)
                      if i.start_ms == start), None)  # fmt: skip
        if match is None:
            return None
        reach = (match.end_ms or now) + MERGE_GAP_MS + 1
        if reach <= window_end or match.end_ms is None:
            return match
        window_end = reach
    return match


@router.get("")
async def get_map(
    request: Request,
    at: int | None = Query(default=None, description="epoch ms; omit for live"),
    incident: str | None = Query(default=None, description="incident id (i<start_ms>)"),
    event: int | None = Query(default=None, description="an event of the incident"),
    show_offline: bool = False,
    traffic_hours: int | None = Query(
        default=None, description="show bytes moved in this window (1, 24, 168 or 720 hours)"
    ),
) -> dict:
    st = request.app.state
    now = int(time.time() * 1000)
    if traffic_hours is not None and traffic_hours not in (1, 24, 168, 720):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "traffic_hours must be 1, 24, 168 or 720"
        )
    if at is not None and not (0 < at <= now):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "at must be in the past")
    inc = None
    if incident is not None or event is not None:
        inc = await asyncio.to_thread(_incident, st, incident, event, now)
        if inc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
        if at is None:
            # Replay a moment inside the incident, where the failure is visible.
            end = inc.end_ms or now
            at = min(inc.start_ms + 30_000, (inc.start_ms + end) // 2) if inc.end_ms else None
    graph = await asyncio.to_thread(
        lambda: st.netmap.build(
            at_ms=at, now_ms=now, show_offline=show_offline, traffic_hours=traffic_hours
        )
    )
    if inc is not None:
        graph["highlight"] = st.netmap.highlight(inc.as_dict(now), graph)
    return graph


Side = Literal["top", "bottom", "left", "right"]


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float


class LayoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positions: dict[str, Position] = Field(max_length=MAX_NODES)
    # Groups shown as one node per parent (keys "<group id>@<parent node id>" or "<group id>").
    collapsed: list[str] | None = Field(default=None, max_length=MAX_NODES)
    # Which side of a tile its links attach to (node id → side); a tile not listed is automatic.
    # Omitted keeps what is stored; an empty object clears every pin.
    pins: dict[str, Side] | None = Field(default=None, max_length=MAX_NODES)


class MapView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    colour_by: Literal["group", "status", "bandwidth"] = "group"
    groups: list[int] = Field(default=[], max_length=50)
    tags: list[str] = Field(default=[], max_length=20)
    collapsed: list[str] = Field(default=[], max_length=200)
    show_offline: bool = False


class ViewsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    views: list[MapView] = Field(max_length=MAX_VIEWS)


@router.get("/layout")
async def get_layout(request: Request) -> dict:
    saved = await asyncio.to_thread(request.app.state.store.kv_get, LAYOUT_KEY, None)
    return {
        "positions": (saved or {}).get("positions", {}),
        "collapsed": (saved or {}).get("collapsed", []),
        "pins": (saved or {}).get("pins", {}),
    }


@router.get("/views")
async def get_views(request: Request) -> dict:
    return {"views": await asyncio.to_thread(request.app.state.store.kv_get, VIEWS_KEY, []) or []}


@router.put("/views")
async def put_views(body: ViewsIn, request: Request) -> dict:
    names = [v.name.strip().lower() for v in body.views]
    if len(set(names)) != len(names):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "view names must be unique")
    views = [v.model_dump() for v in body.views]
    await asyncio.to_thread(request.app.state.store.kv_set, VIEWS_KEY, views)
    return {"views": views}


@router.put("/layout")
async def put_layout(body: LayoutIn, request: Request) -> dict:
    positions = {}
    for nid, p in body.positions.items():
        if not nid or len(nid) > 200:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad node id")
        if not all(math.isfinite(v) and abs(v) <= MAX_COORD for v in (p.x, p.y)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "position out of range")
        positions[nid] = {"x": round(p.x, 1), "y": round(p.y, 1)}
    store = request.app.state.store
    saved = await asyncio.to_thread(store.kv_get, LAYOUT_KEY, None) or {}
    collapsed = (
        saved.get("collapsed", [])
        if body.collapsed is None
        else [c[:200] for c in body.collapsed if c]
    )
    if body.pins is None:
        pins = saved.get("pins", {})
    else:
        if any(not nid or len(nid) > 200 for nid in body.pins):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad node id")
        pins = dict(body.pins)
    await asyncio.to_thread(
        store.kv_set, LAYOUT_KEY, {"positions": positions, "collapsed": collapsed, "pins": pins}
    )
    return {"positions": positions, "collapsed": collapsed, "pins": pins}


@router.delete("/layout", status_code=status.HTTP_204_NO_CONTENT)
async def reset_layout(request: Request) -> None:
    await asyncio.to_thread(request.app.state.store.kv_set, LAYOUT_KEY, None)
