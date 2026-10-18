"""Network map: nodes and links for the visual map, live or as they were at a past moment.

Structure: Internet ← Bell ← Home Hub; the Hub feeds the switch (TrueNAS behind it) and the
main Deco, satellites hang off the main Deco (wired or wireless backhaul), and devices hang
off the Deco they are connected to (from the Deco API) or off the Hub when that is unknown.
Only "key" devices (monitored or named by you) are nodes; other online devices are counted in
a group node per parent so the map stays readable.
"""

from __future__ import annotations

import time
from collections import defaultdict

from .diagnosis import (
    DEVICE,
    GATEWAY,
    GATEWAY_DNS,
    HOST,
    INTERNET,
    ISP,
    LAN,
    MESH,
    PUBLIC_DNS,
    target_layer,
)
from .events import LOSS, OUTAGE, WIFI
from .stats import loss_pct

_RANK = {"down": 3, "degraded": 2, "up": 1, "unknown": 0}
REPLAY_WINDOW_MS = 60_000

LAYER_NODE = {
    HOST: "truenas",
    LAN: "switch",
    GATEWAY: "hub",
    GATEWAY_DNS: "hub",
    ISP: "isp",
    INTERNET: "internet",
    PUBLIC_DNS: "internet",
}


def _worst(states: list[str]) -> str:
    return max(states, key=lambda s: _RANK.get(s, 0)) if states else "unknown"


class NetMap:
    def __init__(self, state) -> None:
        self.st = state  # app.state: store, monitor, nic, discovery, deco, events, diagnoser

    # ── target state, live or replayed ───────────────────────────────────────

    def _target_view(self, targets: list[dict], at_ms: int | None, now: int) -> dict[int, dict]:
        out: dict[int, dict] = {}
        if at_ms is None:
            for t in targets:
                s = self.st.monitor.target_status(t["id"], now)
                stats = s.get("stats") or {}
                out[t["id"]] = {
                    "state": s["state"],
                    "median_ms": stats.get("median"),
                    "loss_pct": stats.get("loss_pct"),
                }
            return out
        failing = {}
        for e in self.st.events.list(at_ms, at_ms + 1, kinds=[OUTAGE, LOSS], limit=2000):
            if e["target_id"] is not None:
                state = "down" if e["kind"] == OUTAGE else "degraded"
                failing[e["target_id"]] = _worst([failing.get(e["target_id"], "up"), state])
        for t in targets:
            st = self.st.diagnoser.window_stats(
                t["id"], at_ms - REPLAY_WINDOW_MS, at_ms + REPLAY_WINDOW_MS, now
            )
            has = bool(st["count"])
            out[t["id"]] = {
                "state": failing.get(t["id"], "up" if has else "unknown"),
                "median_ms": st.get("median"),
                "loss_pct": loss_pct(st),
            }
        return out

    # ── build ────────────────────────────────────────────────────────────────

    def build(self, *, at_ms: int | None = None, now_ms: int | None = None) -> dict:
        now = now_ms or int(time.time() * 1000)
        st = self.st
        route = st.discovery.gateway_ip()
        targets = st.store.list_targets(enabled_only=True)
        target_by_id = {t["id"]: t for t in targets}
        tview = self._target_view(targets, at_ms, now)
        by_layer: dict[str, list[dict]] = defaultdict(list)
        for t in targets:
            by_layer[target_layer(t, route)].append(t)

        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def member(t: dict) -> dict:
            v = tview.get(t["id"], {})
            return {"target_id": t["id"], "name": t["name"], "host": t["host"], **v}

        def agg(ts: list[dict]) -> dict:
            views = [tview.get(t["id"], {}) for t in ts]
            meds = [v["median_ms"] for v in views if v.get("median_ms") is not None]
            losses = [v["loss_pct"] for v in views if v.get("loss_pct") is not None]
            return {
                "state": _worst([v.get("state", "unknown") for v in views]),
                "median_ms": min(meds) if meds else None,
                "loss_pct": max(losses) if losses else None,
            }

        def node(nid: str, kind: str, label: str, **kw) -> dict:
            n = {
                "id": nid,
                "kind": kind,
                "label": label,
                "state": "unknown",
                "inferred": False,
                "members": [],
                "info": {},
                **kw,
            }
            nodes[nid] = n
            return n

        def edge(src: str, dst: str, medium: str = "wired", **kw) -> None:
            edges.append(
                {
                    "id": f"{src}>{dst}",
                    "source": src,
                    "target": dst,
                    "medium": medium,
                    "median_ms": None,
                    "loss_pct": None,
                    "label": None,
                    **kw,
                }
            )

        # Core chain
        inet_ts = by_layer[INTERNET] + by_layer[PUBLIC_DNS]
        a = agg(by_layer[INTERNET]) if by_layer[INTERNET] else agg(inet_ts)
        node(
            "internet",
            "internet",
            "Internet",
            state=a["state"],
            members=[member(t) for t in inet_ts],
        )
        hub_snap = getattr(getattr(st, "hub", None), "last", None) if at_ms is None else None
        isp_state = "down" if hub_snap and hub_snap.get("wan_up") is False else a["state"]
        node("isp", "isp", "Bell network", inferred=not hub_snap, state=isp_state)
        hub_ts = by_layer[GATEWAY] + by_layer[GATEWAY_DNS]
        h = agg(by_layer[GATEWAY]) if by_layer[GATEWAY] else agg(hub_ts)
        node(
            "hub",
            "hub",
            "Bell Home Hub",
            state=h["state"],
            members=[member(t) for t in hub_ts],
            info={"ip": route, **self._hub_info()},
        )
        nics = [n for n in st.nic.status() if n.get("present")]
        host_state = (
            "unknown"
            if not nics or at_ms is not None
            else "up"
            if all(n.get("operstate") == "up" for n in nics)
            else "down"
        )
        node(
            "switch",
            "switch",
            "Switch",
            inferred=True,
            state="up" if h["state"] in ("up", "degraded") else h["state"],
        )
        node(
            "truenas",
            "host",
            "TrueNAS (NetWatch)",
            state=host_state,
            info={
                "interfaces": [
                    {"name": n["iface"], "speed": n.get("speed"), "state": n.get("operstate")}
                    for n in nics
                ]
            },
        )
        edge("hub", "isp", "logical")
        edge("isp", "internet", "logical", median_ms=a["median_ms"], loss_pct=a["loss_pct"])
        edges[-1]["label"] = "internet"
        edge("hub", "switch")
        edge("switch", "truenas", median_ms=h["median_ms"], loss_pct=h["loss_pct"])

        # Devices from the inventory (merged groups resolved)
        rows = st.discovery.inventory.listing(now)
        device_by_mac = {m: d for d in rows for m in d["macs"]}
        target_device = {d["target_id"]: d for d in rows if d.get("target_id")}

        # Decos: from the Deco API when configured, else from mesh targets / AP devices
        deco_state = st.deco.decos if st.deco and st.deco.decos else {}
        deco_events = {}
        if at_ms is not None:
            for e in st.events.list(at_ms, at_ms + 1, kinds=[WIFI], limit=500):
                if e["details"].get("type") == "deco_offline":
                    deco_events[e["details"].get("deco_mac")] = "down"
        mesh_targets = {t["id"]: t for t in by_layer[MESH]}
        deco_nodes: dict[str, str] = {}  # deco mac → node id
        main_id = None
        for mac, d in sorted(deco_state.items(), key=lambda kv: kv[1]["role"] != "main"):
            dev = device_by_mac.get(mac)
            t = (dev and dev.get("target_id") and target_by_id.get(dev["target_id"])) or None
            if at_ms is not None:
                state = deco_events.get(mac) or (tview.get(t["id"], {}).get("state") if t else "up")
            else:
                state = "up" if d["online"] else "down"
                if t and tview.get(t["id"], {}).get("state") in ("down", "degraded"):
                    state = tview[t["id"]]["state"]
            nid = f"deco:{mac}"
            node(
                nid,
                "deco",
                f"Deco {d['name']}",
                state=state,
                members=[member(t)] if t else [],
                info={
                    "mac": mac,
                    "ip": d["ip"],
                    "model": d["model"],
                    "role": d["role"],
                    "backhaul": d["backhaul"],
                    "firmware": d["firmware"],
                    "device_id": dev["id"] if dev else None,
                },
            )
            deco_nodes[mac] = nid
            if t:
                mesh_targets.pop(t["id"], None)
            if d["role"] == "main" and main_id is None:
                main_id = nid
                edge("hub", nid, "wired", **self._edge_stats(t, tview))
            else:
                wired = d["backhaul"] == "wired"
                edge(
                    main_id or "hub",
                    nid,
                    "wired" if wired else "wireless",
                    **self._edge_stats(t, tview),
                    label=None if wired else d["backhaul"],
                )
        # Mesh targets with no Deco API data (or Decos not matched): one node each
        for t in mesh_targets.values():
            nid = f"target:{t['id']}"
            v = tview.get(t["id"], {})
            node(
                nid,
                "deco",
                t["name"],
                state=v.get("state", "unknown"),
                members=[member(t)],
                info={"ip": t["host"], "device_id": (target_device.get(t["id"]) or {}).get("id")},
            )
            edge(main_id or "hub", nid, "logical", **self._edge_stats(t, tview))

        # Key devices and groups
        groups: dict[str, list[dict]] = defaultdict(list)
        for d in rows:
            det = d.get("details") or {}
            if det.get("is_deco") or any(m in deco_state for m in d["macs"]):
                continue
            if d.get("type") == "router" or (route and d.get("last_ip") == route):
                continue  # the Hub itself
            if d.get("type_reason") == "this NetWatch host":
                continue
            parent = deco_nodes.get(det.get("deco_mac") or "") or "hub"
            key = bool(d.get("target_id") or d.get("name"))
            if not key:
                if d["online"]:
                    groups[parent].append(d)
                continue
            t = target_by_id.get(d["target_id"]) if d.get("target_id") else None
            v = tview.get(t["id"], {}) if t else {}
            state = v.get("state", "unknown") if t else "up" if d["online"] else "unknown"
            nid = f"dev:{d['id']}"
            node(
                nid,
                "device",
                d["label"],
                state=state,
                members=[member(t)] if t else [],
                info={
                    "device_id": d["id"],
                    "ip": d.get("last_ip"),
                    "type": d.get("type"),
                    "vendor": d.get("vendor"),
                    "connection": det.get("connection"),
                    "online": d["online"],
                },
            )
            conn = det.get("connection")
            medium = ("wired" if conn == "wired" else "wireless") if parent != "hub" else "logical"
            edge(
                parent,
                nid,
                medium,
                **self._edge_stats(t, tview),
                label=conn if medium == "wireless" else None,
            )
        for parent, members in groups.items():
            nid = f"group:{parent}"
            node(
                nid,
                "group",
                f"{len(members)} more device{'s' if len(members) != 1 else ''}",
                state="up",
                info={
                    "count": len(members),
                    "devices": [
                        {
                            "device_id": m["id"],
                            "label": m["label"],
                            "ip": m.get("last_ip"),
                            "type": m.get("type"),
                            "connection": (m.get("details") or {}).get("connection"),
                        }
                        for m in sorted(members, key=lambda m: m["label"].lower())
                    ],
                },
            )
            edge(parent, nid, "logical")

        if at_ms is None and st.deco and st.deco.clients:
            self._add_traffic(nodes, edges, rows)

        return {
            "generated_ms": now,
            "at_ms": at_ms,
            "deco_api": bool(deco_state),
            "nodes": list(nodes.values()),
            "edges": edges,
        }

    def _add_traffic(self, nodes: dict, edges: list, rows: list[dict]) -> None:
        """Live rates from the Decos on every node, summed up the tree, and on each link."""
        device_of = {m: d["id"] for d in rows for m in d["macs"]}
        own: dict[str, list[float]] = {}

        def add(nid: str, d: float, u: float) -> None:
            r = own.setdefault(nid, [0.0, 0.0])
            r[0] += d
            r[1] += u

        groups = {
            dev["device_id"]: n["id"]
            for n in nodes.values()
            if n["kind"] == "group"
            for dev in n["info"].get("devices", [])
        }
        for mac, c in self.st.deco.clients.items():
            did = device_of.get(mac)
            nid = f"dev:{did}" if did is not None and f"dev:{did}" in nodes else groups.get(did)
            if nid is None:  # e.g. a device not in the inventory yet: count it on its Deco
                nid = f"deco:{c['deco_mac']}" if f"deco:{c['deco_mac']}" in nodes else "hub"
            add(nid, c["down_bps"], c["up_bps"])
        children: dict[str, list[str]] = {}
        for e in edges:
            if e["target"] not in ("isp", "internet"):
                children.setdefault(e["source"], []).append(e["target"])

        def total(nid: str, seen: frozenset = frozenset()) -> list[float]:
            t = list(own.get(nid, [0.0, 0.0]))
            for ch in children.get(nid, []):
                if ch not in seen:
                    sub = total(ch, seen | {nid})
                    t[0] += sub[0]
                    t[1] += sub[1]
            return t

        for nid, n in nodes.items():
            if n["kind"] in ("internet", "isp", "switch", "host"):
                continue
            d, u = total(nid)
            if d or u:
                n["traffic"] = {"down_bps": round(d), "up_bps": round(u)}
        hub = nodes.get("hub", {}).get("traffic")
        for e in edges:
            t = nodes.get(e["target"], {}).get("traffic")
            if e["target"] in ("isp", "internet") and hub:
                t = hub  # everything the Decos carry goes through the Hub to Bell
            if t:
                e["traffic"] = t

    def _hub_info(self) -> dict:
        """The Hub's own report (model, firmware, uptime, fibre levels), when connected."""
        hub = getattr(self.st, "hub", None)
        snap = hub.last if hub else None
        if not snap:
            return {}
        opt = snap.get("optical") or {}
        return {
            "model": snap.get("model"),
            "firmware": snap.get("firmware"),
            "uptime_s": snap.get("uptime_s"),
            "rx_dbm": opt.get("rx_dbm"),
            "tx_dbm": opt.get("tx_dbm"),
            "wan_up": snap.get("wan_up"),
            "wan_ip": snap.get("wan_ip"),
        }

    @staticmethod
    def _edge_stats(t: dict | None, tview: dict) -> dict:
        if not t:
            return {}
        v = tview.get(t["id"], {})
        return {"median_ms": v.get("median_ms"), "loss_pct": v.get("loss_pct")}

    # ── diagnosis highlight ──────────────────────────────────────────────────

    def highlight(self, incident: dict, graph: dict) -> dict:
        """Node ids to highlight for an incident: its most likely cause and what failed."""
        ids = {n["id"] for n in graph["nodes"]}
        failing_targets = {f["target_id"] for f in incident["failing"]}
        failing_nodes = sorted(
            n["id"]
            for n in graph["nodes"]
            if any(m.get("target_id") in failing_targets for m in n["members"])
        )
        cause = incident["causes"][0] if incident["causes"] else None
        cause_nodes: list[str] = []
        if cause:
            layer = cause["layer"]
            if layer in LAYER_NODE and LAYER_NODE[layer] in ids:
                cause_nodes = [LAYER_NODE[layer]]
            elif layer in (MESH, DEVICE):
                by_id = {n["id"]: n for n in graph["nodes"]}
                kinds = ("deco",) if layer == MESH else ("device", "deco")
                candidates = [n for n in graph["nodes"] if n["kind"] in kinds]
                # Most specific first: the unit named in the cause, then units that were
                # down, then failing ones; a mesh-wide cause with none of those marks them all.
                named = [n["id"] for n in candidates if n["label"] in cause["title"]]
                down = [n["id"] for n in candidates if n["kind"] == "deco" and n["state"] == "down"]
                failing = [i for i in failing_nodes if by_id[i]["kind"] in kinds]
                cause_nodes = named or (down if layer == MESH else []) or failing
                if not cause_nodes and layer == MESH:
                    cause_nodes = [n["id"] for n in candidates]
        return {
            "incident_id": incident["id"],
            "start_ms": incident["start_ms"],
            "cause": cause,
            "cause_nodes": cause_nodes,
            "failing_nodes": failing_nodes,
        }
