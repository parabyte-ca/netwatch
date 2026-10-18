"""Per-device traffic: live rates (top talkers now), totals over a period, device history."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query, Request, status

router = APIRouter(prefix="/api")


def _devices_by_mac(st, now_ms: int) -> dict[str, dict]:
    return {m: d for d in st.discovery.inventory.listing(now_ms) for m in d["macs"]}


def _entry(d: dict | None, mac: str) -> dict:
    if d is None:
        return {"device_id": None, "label": mac, "type": "unknown", "macs": [mac]}
    return {"device_id": d["id"], "label": d["label"], "type": d["type"], "macs": d["macs"]}


def live(st, now_ms: int) -> dict:
    deco = st.deco
    by_mac = _devices_by_mac(st, now_ms)
    devices: dict[str, dict] = {}
    by_deco: dict[str, dict] = {}
    for mac, c in deco.clients.items():
        d = by_mac.get(mac)
        key = f"d{d['id']}" if d else mac
        e = devices.setdefault(
            key,
            {
                **_entry(d, mac),
                "down_bps": 0.0,
                "up_bps": 0.0,
                "deco_mac": c["deco_mac"],
                "connection": c["connection"],
            },
        )
        e["down_bps"] += c["down_bps"]
        e["up_bps"] += c["up_bps"]
        b = by_deco.setdefault(c["deco_mac"], {"down_bps": 0.0, "up_bps": 0.0, "clients": 0})
        b["down_bps"] += c["down_bps"]
        b["up_bps"] += c["up_bps"]
        b["clients"] += 1
    rows = sorted(devices.values(), key=lambda e: -(e["down_bps"] + e["up_bps"]))
    for e in rows:
        dm = e["deco_mac"]
        e["deco_name"] = deco.decos[dm]["name"] if dm in deco.decos else None
    return {
        "generated_ms": now_ms,
        "deco_api": bool(deco.decos),
        "as_of_ms": deco.last_ok_ms,
        "down_bps": sum(e["down_bps"] for e in rows),
        "up_bps": sum(e["up_bps"] for e in rows),
        "devices": rows,
        "by_deco": by_deco,
    }


@router.get("/traffic/live")
async def traffic_live(request: Request) -> dict:
    return await asyncio.to_thread(live, request.app.state, int(time.time() * 1000))


@router.get("/traffic/top")
async def traffic_top(request: Request, hours: int = Query(default=24, ge=1, le=24 * 90)) -> dict:
    st = request.app.state
    now = int(time.time())

    def build() -> dict:
        floor = now - st.settings.traffic_retention_days * 86400
        totals = st.traffic.totals(now - hours * 3600, now, minute_floor_s=floor)
        by_mac = _devices_by_mac(st, now * 1000)
        devices: dict[str, dict] = {}
        for mac, t in totals.items():
            d = by_mac.get(mac)
            key = f"d{d['id']}" if d else mac
            e = devices.setdefault(
                key,
                {
                    **_entry(d, mac),
                    "down_bytes": 0.0,
                    "up_bytes": 0.0,
                    "down_peak": 0.0,
                    "up_peak": 0.0,
                },
            )
            e["down_bytes"] += t["down_bytes"]
            e["up_bytes"] += t["up_bytes"]
            e["down_peak"] = max(e["down_peak"], t["down_peak"])
            e["up_peak"] = max(e["up_peak"], t["up_peak"])
        rows = sorted(devices.values(), key=lambda e: -(e["down_bytes"] + e["up_bytes"]))
        return {
            "hours": hours,
            "down_bytes": sum(e["down_bytes"] for e in rows),
            "up_bytes": sum(e["up_bytes"] for e in rows),
            "devices": rows[:50],
        }

    return await asyncio.to_thread(build)


@router.get("/devices/{device_id}/traffic")
async def device_traffic(
    device_id: int, request: Request, hours: int = Query(default=24, ge=1, le=24 * 90)
) -> dict:
    st = request.app.state
    now = int(time.time())

    def build() -> dict | None:
        dev = st.device_store.get(device_id)
        if dev is None:
            return None
        primary_id = dev.get("merged_into") or dev["id"]
        primary = st.device_store.get(primary_id) or dev
        macs = [primary["mac"], *(m["mac"] for m in st.device_store.members(primary["id"]))]
        span = hours * 3600
        step = max(60, (span // 360) // 60 * 60)
        start = (now - span) // step * step
        floor = now - st.settings.traffic_retention_days * 86400
        series = st.traffic.series(macs, start, now, step, minute_floor_s=floor)
        totals = st.traffic.totals(now - span, now, minute_floor_s=floor)
        return {
            "device_id": primary["id"],
            "hours": hours,
            "step": step,
            "series": series,
            "down_bytes": sum(totals.get(m, {}).get("down_bytes", 0) for m in macs),
            "up_bytes": sum(totals.get(m, {}).get("up_bytes", 0) for m in macs),
        }

    out = await asyncio.to_thread(build)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return out
