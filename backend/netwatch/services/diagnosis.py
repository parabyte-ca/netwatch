"""Diagnosis engine: groups failures into incidents and explains each with ranked causes.

Every cause is produced by a small, explainable rule that looks at which layers of the network
failed and which kept working during the incident, plus the TrueNAS NIC, network anomalies and
path tests around it. A cause carries a confidence (0-100), the evidence it used and next steps.
Nothing is stored: incidents are rebuilt from the event timeline on every request.
"""

from __future__ import annotations

import bisect
import ipaddress
import itertools
import statistics
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .events import ANOMALY, LOSS, NIC_ERRORS, NIC_LINK, NIC_SPEED, OUTAGE, WIFI, EventLog
from .netinfo import DefaultRoute
from .pathtest import PathStore
from .stats import aggregate, merge
from .storage import WATERMARK_KEY, Store

MERGE_GAP_MS = 60_000  # failures this close together are one incident
DONE_CACHE = 2000  # finished incidents kept diagnosed in memory
_NO_WIFI = {"macs": [], "deco_mac": None, "deco_name": None}
CONTEXT_PAD_MS = 5 * 60_000  # NIC events and path tests considered around an incident
ANOMALY_PAD_MS = 3600_000
HEALTHY_MAX_LOSS = 20.0  # % loss during the incident below which a target counts as working

# Network layers, in dependency order (a failure lower down takes everything above it with it).
HOST, LAN, GATEWAY, GATEWAY_DNS, MESH, DEVICE, ISP, INTERNET, PUBLIC_DNS = (
    "host", "lan", "gateway", "gateway_dns", "mesh", "device", "isp", "internet", "public_dns",
)  # fmt: skip

LAYER_LABEL = {
    HOST: "TrueNAS network link",
    LAN: "Switch and cabling",
    GATEWAY: "Bell Home Hub",
    GATEWAY_DNS: "Home Hub DNS",
    MESH: "Deco mesh",
    DEVICE: "Devices",
    ISP: "Bell network",
    INTERNET: "Internet destinations",
    PUBLIC_DNS: "Public DNS",
}

# Tree for the UI: (layer, parent)
TOPOLOGY = (
    (HOST, None),
    (LAN, HOST),
    (GATEWAY, LAN),
    (GATEWAY_DNS, GATEWAY),
    (MESH, GATEWAY),
    (DEVICE, MESH),
    (ISP, GATEWAY),
    (INTERNET, ISP),
    (PUBLIC_DNS, ISP),
)


def target_layer(t: dict, gateway_ip: str | None) -> str:
    g, host = t["group"], t["host"]
    if t["kind"] == "dns":
        return GATEWAY_DNS if host == gateway_ip else PUBLIC_DNS
    if g == "gateway" or (gateway_ip and host == gateway_ip):
        return GATEWAY
    if g == "mesh":
        return MESH
    if g == "device":
        return DEVICE
    if g == "public":
        return INTERNET
    try:
        private = ipaddress.ip_address(host).is_private
    except ValueError:
        private = False
    return DEVICE if private else INTERNET


@dataclass
class Cause:
    id: str
    title: str
    layer: str
    confidence: int
    evidence: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


@dataclass
class Incident:
    start_ms: int
    end_ms: int | None
    events: list[dict]
    failing: dict[int, dict] = field(default_factory=dict)  # target_id → {name, layer, kinds}
    healthy: dict[int, dict] = field(default_factory=dict)
    nic_events: list[dict] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)
    path_tests: list[dict] = field(default_factory=list)
    wifi_events: list[dict] = field(default_factory=list)
    causes: list[Cause] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"i{self.start_ms}"

    def duration_ms(self, now_ms: int) -> int:
        return (self.end_ms or now_ms) - self.start_ms

    def layers(self, which: dict) -> set[str]:
        return {v["layer"] for v in which.values()}

    def as_dict(self, now_ms: int) -> dict:
        return {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "open": self.end_ms is None,
            "duration_ms": self.duration_ms(now_ms),
            "event_ids": [e["id"] for e in self.events],
            "severity": "critical" if any(e["kind"] == OUTAGE for e in self.events) else "warning",
            "failing": [{"target_id": k, **v} for k, v in self.failing.items()],
            "healthy": [{"target_id": k, **v} for k, v in self.healthy.items()],
            "failing_layers": sorted(self.layers(self.failing)),
            "nic_events": [_brief(e) for e in self.nic_events],
            "anomalies": [_brief(e) for e in self.anomalies],
            "wifi_events": [_brief(e) for e in self.wifi_events],
            "path_tests": [
                {k: t.get(k) for k in ("id", "ts_ms", "host", "first_loss_hop", "reached")}
                for t in self.path_tests
            ],
            "causes": [asdict(c) for c in self.causes],
        }


def _brief(e: dict) -> dict:
    return {k: e[k] for k in ("id", "kind", "severity", "start_ms", "end_ms", "title")} | {
        "type": e["details"].get("type")
    }


def _overlaps(e: dict, start: int, end: int) -> bool:
    return e["start_ms"] <= end and (e["end_ms"] is None or e["end_ms"] >= start)


class Diagnoser:
    def __init__(
        self,
        store: Store,
        events: EventLog,
        paths: PathStore,
        *,
        gateway: Callable[[], DefaultRoute | None],
        raw_retention_days: int = 7,
        tz: str = "America/Toronto",
        devices=None,
    ) -> None:
        self.store = store
        self.events = events
        self.paths = paths
        self.gateway = gateway
        self.raw_retention_days = raw_retention_days
        self.tz = ZoneInfo(tz)
        self.devices = devices  # DeviceStore: which Deco a monitored device was on
        # Incidents that ended long enough ago that nothing around them can change any more
        # are diagnosed once: a poll then only works on open and recent incidents.
        self._done: OrderedDict[tuple, Incident] = OrderedDict()

    # ── incidents ────────────────────────────────────────────────────────────

    def incidents(self, start_ms: int, end_ms: int, now_ms: int | None = None) -> list[Incident]:
        """Incidents overlapping [start_ms, end_ms), oldest first, each diagnosed."""
        now = now_ms or int(time.time() * 1000)
        failures = self.events.list(start_ms, end_ms, kinds=[OUTAGE, LOSS], limit=5000)
        failures.sort(key=lambda e: e["start_ms"])
        groups: list[Incident] = []
        for ev in failures:
            ev_end = ev["end_ms"]
            cur = groups[-1] if groups else None
            cur_end = None if cur is None else (cur.end_ms if cur.end_ms is not None else now)
            if cur is not None and ev["start_ms"] <= cur_end + MERGE_GAP_MS:
                cur.events.append(ev)
                if cur.end_ms is not None:
                    cur.end_ms = None if ev_end is None else max(cur.end_ms, ev_end)
            else:
                groups.append(Incident(ev["start_ms"], ev_end, [ev]))
        if not groups:
            return []
        targets = {t["id"]: t for t in self.store.list_targets()}
        route = self.gateway()
        gw = route.gateway if route else None
        sig = (
            gw,
            tuple((t["id"], t["host"], t["group"], t["kind"], t["name"]) for t in targets.values()),
        )
        out: list[Incident] = []
        todo: list[Incident] = []
        for inc in groups:
            key = (inc.start_ms, inc.end_ms, tuple(e["id"] for e in inc.events), sig)
            done = self._done.get(key)
            if done is not None:
                self._done.move_to_end(key)
                out.append(done)
            else:
                out.append(inc)
                todo.append(inc)
        if todo:
            ctx_events = self.events.list(
                todo[0].start_ms - ANOMALY_PAD_MS,
                max((g.end_ms or now) for g in todo) + ANOMALY_PAD_MS,
                kinds=[NIC_LINK, NIC_SPEED, NIC_ERRORS, ANOMALY, WIFI],
                limit=50_000,
            )
            wifi = self._wifi_map()
            for inc in todo:
                self._context(inc, targets, gw, ctx_events, wifi, now)
                inc.causes = diagnose(inc, now)
                if inc.end_ms is not None and inc.end_ms + ANOMALY_PAD_MS + 60_000 < now:
                    key = (inc.start_ms, inc.end_ms, tuple(e["id"] for e in inc.events), sig)
                    self._done[key] = inc
            while len(self._done) > DONE_CACHE:
                self._done.popitem(last=False)
        return out

    def _context(
        self,
        inc: Incident,
        targets: dict,
        gw: str | None,
        ctx_events: list,
        wifi: dict[int, dict],
        now: int,
    ) -> None:
        start, end = inc.start_ms, inc.end_ms or now
        for ev in inc.events:
            t = targets.get(ev["target_id"])
            if t is None:
                continue
            entry = inc.failing.setdefault(
                t["id"],
                {"name": t["name"], "host": t["host"], "layer": target_layer(t, gw), "kinds": []},
            )
            if ev["kind"] not in entry["kinds"]:
                entry["kinds"].append(ev["kind"])
            if "macs" not in entry:
                entry.update(wifi.get(t["id"], _NO_WIFI))
        for t in targets.values():
            if t["id"] in inc.failing:
                continue
            n, lost = self.window_loss(t["id"], start, end, now)
            lp = round(100 * lost / n, 2) if n else None
            if lp is not None and lp <= HEALTHY_MAX_LOSS:
                inc.healthy[t["id"]] = {
                    "name": t["name"],
                    "host": t["host"],
                    "layer": target_layer(t, gw),
                    "loss_pct": lp,
                    "deco_mac": wifi.get(t["id"], _NO_WIFI)["deco_mac"],
                }
        for e in ctx_events:
            if e["kind"] == ANOMALY:
                if _overlaps(e, start - ANOMALY_PAD_MS, end + ANOMALY_PAD_MS):
                    inc.anomalies.append(e)
            elif _overlaps(e, start - CONTEXT_PAD_MS, end + CONTEXT_PAD_MS):
                (inc.wifi_events if e["kind"] == WIFI else inc.nic_events).append(e)
        tests = self.paths.list(start_ms=start - 60_000, end_ms=end + CONTEXT_PAD_MS, limit=20)
        linked = {t["id"] for t in tests}
        ev_ids = [ev["id"] for ev in inc.events]
        for t in self.paths.list(event_ids=ev_ids, limit=5 * len(ev_ids)):
            if t["id"] not in linked:
                tests.append(t)
                linked.add(t["id"])
        inc.path_tests = [t for t in tests if t["status"] == "ok"]

    def _wifi_map(self) -> dict[int, dict]:
        """target id → MACs of the device behind it and the Deco it was last seen on (one
        pass over the devices for the whole call)."""
        if not self.devices:
            return {}
        rows = self.devices.all()
        members: dict[int, list[dict]] = {}
        for r in rows:
            if r.get("merged_into"):
                members.setdefault(r["merged_into"], []).append(r)
        out = {}
        for dev in rows:
            if not dev.get("target_id"):
                continue
            group = [dev, *members.get(dev["id"], [])]
            on = next((r["details"] for r in group if (r.get("details") or {}).get("deco_mac")), {})
            out[dev["target_id"]] = {
                "macs": [r["mac"] for r in group],
                "deco_mac": on.get("deco_mac"),
                "deco_name": on.get("deco_name"),
            }
        return out

    def window_loss(
        self, target_id: int, start_ms: int, end_ms: int, now_ms: int
    ) -> tuple[int, int]:
        """(probes, lost) over a window, split between rollups and raw samples like
        ``window_stats`` but counted in SQL (the diagnosis only needs the loss)."""
        raw_cutoff = now_ms - self.raw_retention_days * 86_400_000
        if start_ms >= raw_cutoff:
            return self.store.loss_counts(target_id, start_ms, end_ms + 1)
        watermark_ms = int(self.store.kv_get(WATERMARK_KEY, 0) or 0) * 1000
        split = max(min(watermark_ms, end_ms + 1), start_ms)
        n, lost = self.store.rollup_loss_counts(target_id, start_ms // 60_000 * 60, split // 1000)
        if split <= end_ms:
            rn, rlost = self.store.loss_counts(target_id, split, end_ms + 1)
            n, lost = n + rn, lost + rlost
        return n, lost

    def window_stats(self, target_id: int, start_ms: int, end_ms: int, now_ms: int) -> dict:
        """Probe statistics over a window: raw samples where they are kept, 1-minute rollups
        for older data, and raw samples again for minutes not rolled up yet."""
        raw_cutoff = now_ms - self.raw_retention_days * 86_400_000
        if start_ms >= raw_cutoff:
            return self._raw_stats(target_id, start_ms, end_ms + 1)
        watermark_ms = int(self.store.kv_get(WATERMARK_KEY, 0) or 0) * 1000
        split = max(min(watermark_ms, end_ms + 1), start_ms)
        parts = [self.store.rollup_merged(target_id, start_ms // 60_000 * 60, split // 1000)]
        if split <= end_ms:
            parts.append(self._raw_stats(target_id, split, end_ms + 1))
        return merge(parts)

    def _raw_stats(self, target_id: int, start_ms: int, end_ms: int) -> dict:
        rows = self.store.raw_samples(target_id, start_ms, end_ms)
        return aggregate(rtt if ok else None for _, rtt, ok, _e in rows)

    def incident_for_event(self, event_id: int, now_ms: int | None = None) -> Incident | None:
        ev = self.events.get(event_id)
        if ev is None or ev["kind"] not in (OUTAGE, LOSS):
            return None
        now = now_ms or int(time.time() * 1000)
        span_end = (ev["end_ms"] or now) + 1
        for inc in self.incidents(ev["start_ms"] - 6 * 3600_000, span_end, now):
            if any(e["id"] == event_id for e in inc.events):
                return inc
        return None

    # ── patterns ─────────────────────────────────────────────────────────────

    def patterns(self, incidents: list[Incident]) -> list[dict]:
        return find_patterns([i.start_ms for i in incidents], self.tz)

    def local(self, ms: int) -> datetime:
        return datetime.fromtimestamp(ms / 1000, self.tz)


# ── rules ─────────────────────────────────────────────────────────────────────


def _names(entries: dict, layer: str | None = None) -> str:
    names = [v["name"] for v in entries.values() if layer is None or v["layer"] == layer]
    return ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")


def _fmt_dur(ms: int) -> str:
    s = round(ms / 1000)
    if s < 90:
        return f"{s} s"
    return f"{s // 60} min {s % 60:02d} s" if s < 3600 else f"{s / 3600:.1f} h"


def diagnose(inc: Incident, now_ms: int) -> list[Cause]:
    causes: list[Cause] = []
    fail = inc.layers(inc.failing)
    ok = inc.layers(inc.healthy)
    dur = inc.duration_ms(now_ms)
    loss_only = all(e["kind"] == LOSS for e in inc.events)
    what = "packet loss" if loss_only else "outage"

    # Physical link on the TrueNAS side.
    link_down = [e for e in inc.nic_events if e["kind"] == NIC_LINK]
    if link_down:
        flap_only = all("flapped" in e["title"] for e in link_down)
        causes.append(
            Cause(
                "host_link",
                "TrueNAS network link dropped (cable, switch port or NIC)",
                HOST,
                80 if flap_only else 92,
                [e["title"] for e in link_down[:5]],
                [
                    "Reseat or replace the cable between TrueNAS and the switch.",
                    "Move it to another switch port; if drops follow the port, the switch is "
                    "failing.",
                    "Check the TrueNAS kernel log for NIC driver resets (dmesg | grep -i link).",
                ],
            )
        )
    for e in inc.nic_events:
        if e["kind"] == NIC_SPEED and e["severity"] == "warning":
            causes.append(
                Cause(
                    "host_speed",
                    "TrueNAS link renegotiated to a lower speed (damaged cable or port)",
                    HOST,
                    60,
                    [e["title"]],
                    ["Replace the cable (Cat 6 for 2.5/10 Gb) and check the switch port LED."],
                )
            )
            break
    errs = [e for e in inc.nic_events if e["kind"] == NIC_ERRORS]
    if errs:
        causes.append(
            Cause(
                "host_errors",
                "Errors on the TrueNAS link (cabling or port)",
                HOST,
                55,
                [e["title"] for e in errs[:5]],
                ["CRC errors almost always mean a bad cable or connector: replace it first."],
            )
        )

    # Everything including the Home Hub failed.
    if GATEWAY in fail and not (ok - {GATEWAY_DNS, PUBLIC_DNS}) and not link_down:
        causes.append(
            Cause(
                "lan",
                "Local network between TrueNAS and the Home Hub, or the Hub itself",
                LAN,
                72,
                [
                    f"The Home Hub ({_names(inc.failing, GATEWAY)}) stopped answering too.",
                    "No target stayed reachable, and the TrueNAS link itself stayed up.",
                ],
                [
                    "Check the switch's power and LEDs; an unmanaged switch that reboots or "
                    "overheats drops everything behind it.",
                    "Look at the Home Hub's uptime/event log for a restart at this time.",
                    "If it repeats, plug TrueNAS straight into the Hub for a few days to rule "
                    "the switch in or out.",
                ],
            )
        )
        if 90_000 <= dur <= 8 * 60_000 and not loss_only:
            causes.append(
                Cause(
                    "hub_restart",
                    "Home Hub restarted or froze",
                    GATEWAY,
                    55,
                    [f"Everything was down for {_fmt_dur(dur)}, about as long as a Hub restart."],
                    [
                        "Check the Hub's system log / uptime for a reboot at this time.",
                        "Frequent restarts: ask Bell for a Hub firmware check or replacement.",
                    ],
                )
            )
    elif GATEWAY in fail and (ok & {INTERNET, MESH, DEVICE}):
        causes.append(
            Cause(
                "hub_icmp",
                "Home Hub stopped answering pings while traffic still flowed",
                GATEWAY,
                60,
                [f"Still reachable during the {what}: {_names(inc.healthy)}."],
                [
                    "Usually harmless: busy Hubs drop ping replies first. Watch for it lining up "
                    "with real outages."
                ],
            )
        )

    # Bell / beyond the Hub.
    internet_fail = {k: v for k, v in inc.failing.items() if v["layer"] == INTERNET}
    internet_ok = {k: v for k, v in inc.healthy.items() if v["layer"] == INTERNET}
    if internet_fail and GATEWAY not in fail:
        hub_ok = GATEWAY in ok
        evidence = [f"Failing: {_names(internet_fail)}."]
        if hub_ok:
            evidence.append(f"The Home Hub kept answering ({_names(inc.healthy, GATEWAY)}).")
        conf: int
        if not internet_ok and len(internet_fail) >= 2:
            conf = 85 if hub_ok else 70
            hop_note = _path_evidence(inc)
            if hop_note:
                evidence.append(hop_note[0])
                conf += hop_note[1]
            causes.append(
                Cause(
                    "isp",
                    f"Bell network {what} (beyond the Home Hub)",
                    ISP,
                    conf,
                    evidence,
                    [
                        "Note the times and include this report when you contact Bell.",
                        "Check the Hub's internet/fibre status light and its WAN log for this "
                        "time.",
                        "If the fibre light (PON/ONT) went red or off, the problem is on Bell's "
                        "line.",
                    ],
                )
            )
        elif internet_ok:
            causes.append(
                Cause(
                    "destination",
                    "A specific destination or route, not your connection",
                    INTERNET,
                    65,
                    [*evidence, f"Other internet targets were fine: {_names(internet_ok)}."],
                    [
                        "Nothing to fix at home. If it matters, run a path test to that host and "
                        "compare with a working one."
                    ],
                )
            )
        else:
            causes.append(
                Cause(
                    "isp",
                    f"Bell network {what} (only one internet target to compare)",
                    ISP,
                    60 if hub_ok else 50,
                    evidence,
                    [
                        "Add a second public target (e.g. 8.8.8.8 and 1.1.1.1) so NetWatch can "
                        "tell a Bell problem from one destination's."
                    ],
                )
            )

    # DNS only.
    dns_layers = {GATEWAY_DNS, PUBLIC_DNS}
    if fail and fail <= dns_layers:
        dns_anoms = [
            a["title"]
            for a in inc.anomalies
            if a["details"].get("type") in ("dns_mismatch", "nxdomain_rewrite")
        ]
        if fail == {GATEWAY_DNS} and PUBLIC_DNS in ok:
            causes.append(
                Cause(
                    "hub_dns",
                    "Home Hub DNS relay failing (internet itself was fine)",
                    GATEWAY_DNS,
                    85,
                    [
                        f"Failing: {_names(inc.failing)}.",
                        f"Working: {_names(inc.healthy)}.",
                        *dns_anoms,
                    ],
                    [
                        "Set the DNS servers handed out by DHCP (or on your devices) to 1.1.1.1 "
                        "and 8.8.8.8 to bypass the Hub's DNS.",
                    ],
                )
            )
        else:
            causes.append(
                Cause(
                    "dns",
                    "DNS resolution failing while connectivity was fine",
                    PUBLIC_DNS if GATEWAY_DNS not in fail else GATEWAY_DNS,
                    70,
                    [f"Failing: {_names(inc.failing)}.", *dns_anoms],
                    [
                        "Compare resolvers on the Diagnosis page; if every resolver fails, Bell "
                        "may be intercepting DNS."
                    ],
                )
            )

    # Deco mesh and Wi-Fi.
    mesh_fail = {k: v for k, v in inc.failing.items() if v["layer"] == MESH}
    mesh_ok = {k: v for k, v in inc.healthy.items() if v["layer"] == MESH}
    churn = [a["title"] for a in inc.anomalies if a["details"].get("type") == "dhcp_churn"]
    if mesh_fail and GATEWAY not in fail:
        single = len(mesh_fail) == 1 and mesh_ok
        causes.append(
            Cause(
                "mesh_node" if single else "mesh",
                f"{'' if 'deco' in _names(mesh_fail).lower() else 'Deco '}"
                f"{_names(mesh_fail)} (its backhaul link or power)"
                if single
                else "Deco mesh (main Deco or its link to the Hub)",
                MESH,
                min(95, (80 if single else 75) + (8 if churn else 0)),
                [f"Failing: {_names(mesh_fail)}."]
                + ([f"Still fine: {_names(mesh_ok)}."] if mesh_ok else [])
                + (
                    [f"The Home Hub stayed reachable ({_names(inc.healthy, GATEWAY)})."]
                    if GATEWAY in ok
                    else []
                )
                + churn[:3],
                [
                    "Wire the affected Deco's backhaul (Ethernet to the switch) if it is "
                    "wireless, or move it closer.",
                    "Check the Deco app for that unit's connection quality and recent restarts.",
                    "Make sure all Decos run the same firmware; disable auto-update windows that "
                    "match the drop times.",
                ],
            )
        )
    dev_fail = {k: v for k, v in inc.failing.items() if v["layer"] == DEVICE}
    if dev_fail and fail == {DEVICE}:
        if len(dev_fail) == 1:
            causes.append(
                Cause(
                    "device",
                    f"{_names(dev_fail)} itself (sleep, power saving or weak signal)",
                    DEVICE,
                    70,
                    [f"Only {_names(dev_fail)} failed; everything else stayed up.", *churn[:2]],
                    ["Check the device's power-saving / Wi-Fi sleep settings, or wire it."],
                )
            )
        else:
            causes.append(
                Cause(
                    "wifi_clients",
                    "Several devices dropped while the network core stayed up (Wi-Fi)",
                    MESH,
                    68 + (8 if churn else 0),
                    [f"Failing: {_names(dev_fail)}.", *churn[:3]],
                    [
                        "Look for a common Deco: devices on the same unit dropping together point "
                        "at that unit's radio or backhaul."
                    ],
                )
            )

    # Deco API: units going offline, devices sharing a Deco, Wi-Fi drops and roaming.
    wifi = inc.wifi_events
    for e in wifi:
        if e["details"].get("type") == "deco_offline":
            name = e["details"].get("deco") or "A Deco"
            causes.append(
                Cause(
                    "deco_offline",
                    f"Deco {name} went offline (power, restart or lost backhaul)",
                    MESH,
                    92,
                    [e["title"], f"Failing at the same time: {_names(inc.failing)}."],
                    [
                        "Check that unit's power and, if it has a wired backhaul, its cable to "
                        "the switch.",
                        "In the Deco app, look at that unit's recent restarts and firmware "
                        "updates.",
                    ],
                )
            )
    wifi_fail = {k: v for k, v in inc.failing.items() if v.get("deco_mac")}
    by_deco: dict[str, list[str]] = {}
    for v in wifi_fail.values():
        by_deco.setdefault(v["deco_mac"], []).append(v["name"])
    other_ok = {v.get("deco_mac") for v in inc.healthy.values()} - {None}
    if GATEWAY not in fail:
        for mac, names in by_deco.items():
            if len(names) >= 2 and len(by_deco) == 1:
                deco = next(v["deco_name"] for v in wifi_fail.values() if v["deco_mac"] == mac)
                evidence = [f"All on Deco {deco or mac}: {', '.join(names)}."]
                if other_ok - {mac}:
                    evidence.append("Devices on other Decos kept working.")
                causes.append(
                    Cause(
                        "deco_shared",
                        f"Deco {deco or mac} (every failing device was connected to it)",
                        MESH,
                        85 if other_ok - {mac} else 78,
                        evidence,
                        [
                            "Restart that Deco and watch whether the drops follow it.",
                            "Check its backhaul (wire it if possible) and its placement.",
                        ],
                    )
                )
    failing_macs = {m: v["name"] for v in inc.failing.values() for m in v.get("macs") or []}
    for e in wifi:
        typ, mac = e["details"].get("type"), e["details"].get("mac")
        if typ not in ("disconnect", "roam") or mac not in failing_macs:
            continue
        name = failing_macs[mac]
        if typ == "disconnect":
            causes.append(
                Cause(
                    "wifi_disconnect",
                    f"{name} dropped off Wi-Fi",
                    DEVICE,
                    80,
                    [e["title"]],
                    [
                        "Check the device's Wi-Fi power saving and its signal where it sits.",
                        "If it is stationary, wire it or pin it to the nearest Deco.",
                    ],
                )
            )
        else:
            causes.append(
                Cause(
                    "wifi_roam",
                    f"{name} roamed between Decos as it failed",
                    DEVICE,
                    70,
                    [e["title"]],
                    [
                        "Roaming hiccups are usually short; if they recur for a stationary "
                        "device it sits between two Decos — move one or wire the device.",
                    ],
                )
            )

    # Network anomalies around the incident.
    failing_hosts = {v["host"] for v in inc.failing.values()}
    for a in inc.anomalies:
        typ, d = a["details"].get("type"), a["details"]
        near = _overlaps(a, inc.start_ms - 10 * 60_000, (inc.end_ms or now_ms) + 10 * 60_000)
        if typ == "duplicate_ip" and (d.get("ip") in failing_hosts or near):
            causes.append(
                Cause(
                    "ip_conflict",
                    f"IP address conflict on {d.get('ip')}",
                    LAN,
                    90 if d.get("ip") in failing_hosts else 70,
                    [a["title"], f"Devices: {', '.join(d.get('macs') or [])}"],
                    [
                        "Give one of the devices a different address (or a DHCP reservation) so "
                        "they stop fighting over it."
                    ],
                )
            )
        elif typ == "arp_storm" and near:
            causes.append(
                Cause(
                    "loop",
                    "Switching loop or broadcast storm",
                    LAN,
                    85,
                    [a["title"]],
                    [
                        "Look for a cable connecting the switch to itself, or a Deco that is wired "
                        "and also meshing wirelessly to the same network.",
                        "Unplug switch cables one at a time until the storm stops.",
                    ],
                )
            )
        elif typ == "dhcp_server":
            causes.append(
                Cause(
                    "rogue_dhcp",
                    "Second DHCP server on the network (a Deco out of AP mode?)",
                    MESH,
                    75,
                    [a["title"]],
                    [
                        "In the Deco app, confirm every unit is in Access Point mode; restart the "
                        "one that answered DHCP."
                    ],
                )
            )
        elif typ == "gateway_mac" and near:
            causes.append(
                Cause(
                    "gateway_mac",
                    "Something else answered for the gateway's address",
                    LAN,
                    75,
                    [a["title"]],
                    [
                        "If the Hub wasn't replaced, find the device with the new MAC on the "
                        "Devices page."
                    ],
                )
            )

    if not causes:
        causes.append(
            Cause(
                "unknown",
                "Not enough evidence to pin this down yet",
                INTERNET if INTERNET in fail else (min(fail) if fail else ISP),
                15,
                [f"Failing: {_names(inc.failing) or 'none recorded'}."],
                [
                    "Add targets for each layer (Home Hub, each Deco, two public addresses) so "
                    "failures can be compared.",
                ],
            )
        )
    best: dict[str, Cause] = {}
    for c in causes:
        c.confidence = max(1, min(97, c.confidence))
        if c.id not in best or c.confidence > best[c.id].confidence:
            best[c.id] = c
    return sorted(best.values(), key=lambda c: -c.confidence)


def _path_evidence(inc: Incident) -> tuple[str, int] | None:
    for t in inc.path_tests:
        hop = t.get("first_loss_hop")
        hops = t.get("hops") or []
        if hop is None or not hops:
            continue
        host = next((h["host"] for h in hops if h.get("hop") == hop), None) or "?"
        if hop >= 2:
            return (
                f"Path test to {t['host']}: loss starts at hop {hop} ({host}), beyond the "
                "Home Hub.",
                8,
            )
        return (f"Path test to {t['host']}: loss starts at the first hop ({host}).", -10)
    return None


# ── patterns ──────────────────────────────────────────────────────────────────


def find_patterns(starts_ms: list[int], tz: ZoneInfo) -> list[dict]:
    """Regular intervals and time-of-day clustering in incident start times."""
    out: list[dict] = []
    starts = sorted(starts_ms)
    if len(starts) >= 4:
        gaps = [b - a for a, b in itertools.pairwise(starts)]
        ordered = sorted(gaps)
        best: list[int] = []
        for g in gaps:  # O(n log n): the gaps close to g are a slice of the sorted gaps
            tol = max(60_000, 0.05 * g)
            lo, hi = bisect.bisect_left(ordered, g - tol), bisect.bisect_right(ordered, g + tol)
            if hi - lo > len(best):
                best = ordered[lo:hi]
        if len(best) >= 3 and len(best) >= 0.6 * len(gaps):
            period = statistics.median(best)
            out.append(
                {
                    "kind": "interval",
                    "period_s": round(period / 1000),
                    "occurrences": len(best) + 1,
                    "title": f"Failures repeat about every {_fmt_period(period)}",
                    "hint": "A fixed interval points at something scheduled: a DHCP lease "
                    "renewal, a Deco or Hub maintenance task, or a device rebooting on a timer.",
                }
            )
        hours = [datetime.fromtimestamp(s / 1000, tz).hour for s in starts]
        counts = {
            h0: sum(1 for h in hours if (h - h0) % 24 < 3) for h0 in range(24)
        }  # three-hour windows, wrapping past midnight
        # Ties: prefer a window that starts at an hour a failure actually started in.
        h0, n = max(counts.items(), key=lambda kv: (kv[1], kv[0] in hours))
        if n >= 4 and n >= 0.6 * len(hours):
            share = round(100 * n / len(hours))
            out.append(
                {
                    "kind": "time_of_day",
                    "from_hour": h0,
                    "to_hour": (h0 + 3) % 24,
                    "share": share,
                    "title": f"{share} % of failures start between {h0:02d}:00 and "
                    f"{(h0 + 3) % 24:02d}:00",
                    "hint": "A time-of-day cluster points at evening congestion (if it is "
                    "packet loss on Bell's side) or a scheduled task at home (backups, "
                    "updates, Deco optimisation).",
                }
            )
    return out


def _fmt_period(ms: float) -> str:
    s = ms / 1000
    if s < 3600:
        return f"{round(s / 60)} min"
    if s < 86400 * 1.5:
        return f"{s / 3600:.1f} h".replace(".0 h", " h")
    return f"{s / 86400:.1f} days".replace(".0 days", " days")
