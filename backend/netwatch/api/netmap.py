"""Visual network map: the graph (live or at a past moment), incident highlight, saved layout."""

from __future__ import annotations

import asyncio
import math
import time

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/map")

LAYOUT_KEY = "map_layout"
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
    for inc in st.diagnoser.incidents(start - 1, start + 1, now):
        if inc.start_ms == start:
            return inc
    return None


@router.get("")
async def get_map(
    request: Request,
    at: int | None = Query(default=None, description="epoch ms; omit for live"),
    incident: str | None = Query(default=None, description="incident id (i<start_ms>)"),
    event: int | None = Query(default=None, description="an event of the incident"),
) -> dict:
    st = request.app.state
    now = int(time.time() * 1000)
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
    graph = await asyncio.to_thread(st.netmap.build, at_ms=at, now_ms=now)
    if inc is not None:
        graph["highlight"] = st.netmap.highlight(inc.as_dict(now), graph)
    return graph


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float
    y: float


class LayoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positions: dict[str, Position] = Field(max_length=MAX_NODES)


@router.get("/layout")
async def get_layout(request: Request) -> dict:
    saved = await asyncio.to_thread(request.app.state.store.kv_get, LAYOUT_KEY, None)
    return {"positions": (saved or {}).get("positions", {})}


@router.put("/layout")
async def put_layout(body: LayoutIn, request: Request) -> dict:
    positions = {}
    for nid, p in body.positions.items():
        if not nid or len(nid) > 200:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad node id")
        if not all(math.isfinite(v) and abs(v) <= MAX_COORD for v in (p.x, p.y)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "position out of range")
        positions[nid] = {"x": round(p.x, 1), "y": round(p.y, 1)}
    await asyncio.to_thread(request.app.state.store.kv_set, LAYOUT_KEY, {"positions": positions})
    return {"positions": positions}


@router.delete("/layout", status_code=status.HTTP_204_NO_CONTENT)
async def reset_layout(request: Request) -> None:
    await asyncio.to_thread(request.app.state.store.kv_set, LAYOUT_KEY, None)
