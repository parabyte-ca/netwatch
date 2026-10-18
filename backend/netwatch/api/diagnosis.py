"""Diagnosis: network map, diagnosed incidents, patterns and the ISP incident report."""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from ..services.diagnosis import (
    GATEWAY,
    HOST,
    INTERNET,
    ISP,
    LAN,
    LAYER_LABEL,
    TOPOLOGY,
    target_layer,
)
from ..services.netinfo import default_route
from ..services.report import build_report, report_filename

router = APIRouter(prefix="/api")

_RANK = {"down": 3, "degraded": 2, "up": 1, "unknown": 0}


def _range(start: int | None, end: int | None, default_days: int) -> tuple[int, int]:
    end_s = end or int(time.time()) + 1
    start_s = start or end_s - default_days * 86400
    if start_s >= end_s:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start must be before end")
    if end_s - start_s > 366 * 86400:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "range is limited to a year")
    return start_s * 1000, end_s * 1000


def _topology(request: Request) -> list[dict]:
    st = request.app.state
    now = int(time.time() * 1000)
    route = default_route()
    gw = route.gateway if route else None
    members: dict[str, list[dict]] = {layer: [] for layer, _ in TOPOLOGY}
    for t in st.store.list_targets(enabled_only=True):
        s = st.monitor.target_status(t["id"], now)
        members[target_layer(t, gw)].append(
            {"target_id": t["id"], "name": t["name"], "host": t["host"], "state": s["state"]}
        )

    def worst(layer: str) -> str:
        states = [m["state"] for m in members[layer]]
        return max(states, key=lambda s: _RANK[s]) if states else "unknown"

    state = {layer: worst(layer) for layer, _ in TOPOLOGY}
    nics = st.nic.status()
    present = [n for n in nics if n.get("present")]
    if present:
        state[HOST] = "up" if all(n.get("operstate") == "up" for n in present) else "down"
    # Not directly observable: the switch is fine if the Hub answers; Bell is fine if anything
    # on the internet answers.
    state[LAN] = "up" if state[GATEWAY] in ("up", "degraded") else state[GATEWAY]
    state[ISP] = state[INTERNET]
    out = []
    for layer, parent in TOPOLOGY:
        node = {
            "layer": layer,
            "parent": parent,
            "label": LAYER_LABEL[layer],
            "state": state[layer],
            "inferred": layer in (LAN, ISP) or (layer == HOST and not present),
            "members": members[layer],
        }
        if layer == HOST:
            node["members"] = [
                {"name": n["iface"], "state": "up" if n.get("operstate") == "up" else "down"}
                for n in present
            ]
        out.append(node)
    return out


@router.get("/diagnosis")
async def diagnosis(
    request: Request,
    start: int | None = Query(default=None, description="epoch seconds"),
    end: int | None = Query(default=None, description="epoch seconds"),
) -> dict:
    st = request.app.state
    start_ms, end_ms = _range(start, end, 7)
    now = int(time.time() * 1000)

    def build() -> tuple[list, list]:
        incidents = st.diagnoser.incidents(start_ms, end_ms, now)
        return st.diagnoser.patterns(incidents), [i.as_dict(now) for i in incidents[-300:][::-1]]

    patterns, incidents = await asyncio.to_thread(build)
    return {
        "now_ms": now,
        "topology": await asyncio.to_thread(_topology, request),
        "patterns": patterns,
        "incidents": incidents,
    }


@router.get("/diagnosis/event/{event_id}")
async def diagnosis_for_event(event_id: int, request: Request) -> dict:
    now = int(time.time() * 1000)
    inc = await asyncio.to_thread(request.app.state.diagnoser.incident_for_event, event_id, now)
    if inc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no outage or packet-loss incident")
    return inc.as_dict(now)


@router.get("/report", response_model=None)
async def report(
    request: Request,
    start: int | None = None,
    end: int | None = None,
    scope: Literal["all", "isp"] = "all",
    format: Literal["json", "md"] = "json",
) -> dict | PlainTextResponse:
    st = request.app.state
    start_ms, end_ms = _range(start, end, 30)
    r = await asyncio.to_thread(
        build_report, st.diagnoser, st.throughput_store, start_ms, end_ms, scope=scope
    )
    if format == "md":
        return PlainTextResponse(
            r["markdown"],
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{report_filename(r)}"'},
        )
    return r
