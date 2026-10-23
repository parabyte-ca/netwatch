from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ..config import Settings
from ..probes import TargetSpec
from ..probes.base import now_ms
from ..schemas import ProbeResultOut, TargetIn, TargetOut
from ..services.events import LOSS, OUTAGE
from ..services.scheduler import Monitor
from ..services.series import build_series
from ..services.storage import Store
from .deps import get_app_settings, get_monitor, get_store

router = APIRouter(prefix="/api")

MAX_EXPORT_RANGE_S = 31 * 86400


def _require(store: Store, target_id: int) -> dict:
    target = store.get_target(target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target not found")
    return target


def _time_range(start: int | None, end: int | None, default_span: int) -> tuple[int, int]:
    now = int(time.time()) + 1  # include samples from the current, partial second
    end = min(end or now, now + 60)
    start = start if start is not None else end - default_span
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start must be before end")
    return start, end


@router.get("/targets", response_model=list[TargetOut])
def list_targets(store: Store = Depends(get_store)) -> list[dict]:
    return store.list_targets()


@router.post("/targets", response_model=TargetOut, status_code=status.HTTP_201_CREATED)
async def create_target(
    body: TargetIn,
    store: Store = Depends(get_store),
    monitor: Monitor = Depends(get_monitor),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    if await asyncio.to_thread(store.count_targets) >= settings.max_targets:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"target limit ({settings.max_targets}) reached"
        )
    target = await asyncio.to_thread(store.create_target, {**body.model_dump(), "auto": False})
    await monitor.reload()
    return target


@router.get("/targets/{target_id}", response_model=TargetOut)
def get_target(target_id: int, store: Store = Depends(get_store)) -> dict:
    return _require(store, target_id)


@router.put("/targets/{target_id}", response_model=TargetOut)
async def update_target(
    target_id: int,
    body: TargetIn,
    request: Request,
    store: Store = Depends(get_store),
    monitor: Monitor = Depends(get_monitor),
) -> dict:
    target = await asyncio.to_thread(store.update_target, target_id, body.model_dump())
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target not found")
    # A monitored device carries the same name as its target (renaming the device already
    # renames the target), so a rename here shows on the Devices page too.
    devices = getattr(request.app.state, "device_store", None)
    if devices is not None:
        device = await asyncio.to_thread(devices.by_target, target_id)
        if device is not None and device.get("name") != target["name"]:
            await asyncio.to_thread(devices.update, device["id"], {"name": target["name"]})
            request.app.state.discovery.inventory.forget(device["mac"])
    await monitor.reload()
    return target


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    target_id: int, store: Store = Depends(get_store), monitor: Monitor = Depends(get_monitor)
) -> Response:
    if not await asyncio.to_thread(store.delete_target, target_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "target not found")
    await monitor.reload()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/targets/{target_id}/test", response_model=ProbeResultOut)
async def test_target(
    target_id: int, store: Store = Depends(get_store), monitor: Monitor = Depends(get_monitor)
):
    """Run one probe immediately, outside the schedule. The result is not stored."""
    target = await asyncio.to_thread(_require, store, target_id)
    return await monitor.run_once(TargetSpec.from_row(target))


@router.get("/status")
def live_status(store: Store = Depends(get_store), monitor: Monitor = Depends(get_monitor)) -> dict:
    now = now_ms()
    return {
        "now_ms": now,
        "targets": [
            {"id": t["id"], "enabled": t["enabled"], **monitor.target_status(t["id"], now)}
            for t in store.list_targets()
        ],
    }


@router.get("/targets/{target_id}/series")
def series(
    request: Request,
    target_id: int,
    start: int | None = Query(default=None, description="epoch seconds"),
    end: int | None = Query(default=None, description="epoch seconds"),
    points: int = Query(default=720, ge=10, le=5000),
    store: Store = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    target = _require(store, target_id)
    start, end = _time_range(start, end, 3600)
    out = build_series(
        store,
        target,
        start,
        end,
        now=time.time(),
        raw_retention_days=settings.raw_retention_days,
        points=points,
    )
    incidents = request.app.state.events.list(
        start * 1000, end * 1000, kinds=[OUTAGE, LOSS], target_id=target_id
    )
    out["events"] = [
        {"id": e["id"], "kind": e["kind"], "start_ms": e["start_ms"], "end_ms": e["end_ms"]}
        for e in incidents
    ]
    return out


@router.get("/targets/{target_id}/export")
def export(
    target_id: int,
    start: int | None = Query(default=None),
    end: int | None = Query(default=None),
    fmt: Literal["csv", "json"] = Query(default="csv", alias="format"),
    store: Store = Depends(get_store),
) -> StreamingResponse:
    """Raw samples for a range (within raw retention) as CSV or JSON."""
    target = _require(store, target_id)
    start, end = _time_range(start, end, 86400)
    if end - start > MAX_EXPORT_RANGE_S:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "range too large (max 31 days)")

    def rows() -> Iterator[tuple]:
        # An hour at a time: a month at 1 s would otherwise sit in memory (millions of rows).
        for t0 in range(start * 1000, end * 1000, 3_600_000):
            yield from store.raw_samples(target_id, t0, min(t0 + 3_600_000, end * 1000))

    safe = "".join(c if c.isalnum() else "-" for c in target["name"]).strip("-") or "target"
    filename = f"netwatch-{safe}-{start}-{end}.{fmt}"

    def as_csv() -> Iterator[str]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ts_ms", "rtt_ms", "ok", "error"])
        for ts_ms, rtt, ok, err in rows():
            w.writerow([ts_ms, "" if rtt is None else rtt, int(ok), err or ""])
            if buf.tell() > 65536:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    def as_json() -> Iterator[str]:
        yield json.dumps({"target": target, "start": start, "end": end})[:-1] + ', "samples": ['
        for i, (ts_ms, rtt, ok, err) in enumerate(rows()):
            yield ("," if i else "") + json.dumps(
                {"ts_ms": ts_ms, "rtt_ms": rtt, "ok": bool(ok), "error": err}
            )
        yield "]}"

    media = "text/csv" if fmt == "csv" else "application/json"
    return StreamingResponse(
        as_csv() if fmt == "csv" else as_json(),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
