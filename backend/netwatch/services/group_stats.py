"""Per-group totals: devices online, traffic, DNS and alerts over a period (v0.15).

Used by the Devices page ("Groups at a glance") and by the weekly health report.
"""

from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import select

from ..db import device_groups, devices
from .discovery.inventory import ONLINE_MS, device_label
from .events import ANOMALY, DEVICE, DNS, LOSS, OUTAGE

UNGROUPED = {"id": 0, "name": "Ungrouped", "colour": "slate", "icon": "star", "quiet": False}


def group_stats(
    engine,
    *,
    traffic,
    clients,
    events,
    start_ms: int,
    end_ms: int,
    minute_floor_s: int,
    dns: bool,
) -> list[dict]:
    """One row per group (by sort order) plus "Ungrouped", for [start_ms, end_ms)."""
    with engine.connect() as conn:
        order = (device_groups.c.sort, device_groups.c.name)
        groups = [dict(r._mapping) for r in conn.execute(select(device_groups).order_by(*order))]
        rows = [dict(r._mapping) for r in conn.execute(select(devices))]
    by_id = {r["id"]: r for r in rows}
    primary_of = {
        r["id"]: (r["merged_into"] if r["merged_into"] in by_id else r["id"]) for r in rows
    }
    members: dict[int, list[dict]] = defaultdict(list)  # primary id → rows (primary first)
    for r in sorted(rows, key=lambda r: r["merged_into"] is not None):
        members[primary_of[r["id"]]].append(r)
    group_of = {pid: (by_id[pid]["group_id"] or 0) for pid in members}

    bytes_by_mac = traffic.totals(start_ms // 1000, end_ms // 1000, minute_floor_s=minute_floor_s)
    evs = events.list(start_ms, end_ms, kinds=[ANOMALY, DEVICE, DNS, OUTAGE, LOSS], limit=5000)
    mac_to_pid = {r["mac"]: primary_of[r["id"]] for r in rows}
    target_to_pid = {r["target_id"]: primary_of[r["id"]] for r in rows if r["target_id"]}

    def event_pid(e: dict) -> int | None:
        if e.get("target_id") in target_to_pid:
            return target_to_pid[e["target_id"]]
        det = e.get("details") or {}
        if det.get("device_id") in primary_of:
            return primary_of[det["device_id"]]
        return mac_to_pid.get(str(det.get("mac", "")).lower())

    per_group: dict[int, dict] = {}
    for g in [*groups, UNGROUPED]:
        per_group[g["id"]] = {
            "id": g["id"], "name": g["name"], "colour": g["colour"], "icon": g["icon"],
            "quiet": bool(g.get("quiet")), "devices": 0, "online": 0, "down_bytes": 0.0,
            "up_bytes": 0.0, "top_talker": None, "outages": 0, "alerts": 0, "quiet_events": 0,
            "dns": None,
        }  # fmt: skip
    talker: dict[int, tuple[float, str]] = {}
    ids_of: dict[int, list[int]] = defaultdict(list)
    for pid, ms in members.items():
        g = per_group.get(group_of[pid], per_group[0])
        prim = by_id[pid]
        g["devices"] += 1
        if end_ms - max(m["last_seen_ms"] for m in ms) <= ONLINE_MS:
            g["online"] += 1
        d = sum(bytes_by_mac.get(m["mac"], {}).get("down_bytes", 0) for m in ms)
        u = sum(bytes_by_mac.get(m["mac"], {}).get("up_bytes", 0) for m in ms)
        g["down_bytes"] += d
        g["up_bytes"] += u
        if d + u > talker.get(g["id"], (0.0, ""))[0]:
            label = device_label({**prim, "details": _details(prim)})
            talker[g["id"]] = (d + u, label)
        ids_of[g["id"]].extend(m["id"] for m in ms)
    for gid, (_, label) in talker.items():
        per_group[gid]["top_talker"] = label
    for e in evs:
        pid = event_pid(e)
        if pid is None:
            continue
        g = per_group.get(group_of.get(pid, 0), per_group[0])
        if e["kind"] in (OUTAGE, LOSS):
            g["outages"] += 1
        elif (e.get("details") or {}).get("quiet"):
            g["quiet_events"] += 1
        elif e["severity"] in ("warning", "critical"):
            g["alerts"] += 1
    if dns:
        for gid, ids in ids_of.items():
            s = clients.summary(ids, start_ms, end_ms)
            if s["queries"]:
                per_group[gid]["dns"] = {k: s[k] for k in ("queries", "blocked_pct", "failed_pct")}
    out = [per_group[g["id"]] for g in groups]
    if per_group[0]["devices"]:
        out.append(per_group[0])
    for g in out:
        g["down_bytes"], g["up_bytes"] = round(g["down_bytes"]), round(g["up_bytes"])
    return out


def _details(row: dict) -> dict:
    raw = row.get("details")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {}
