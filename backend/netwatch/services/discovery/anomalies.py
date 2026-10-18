"""Network anomaly detectors. Each finding becomes an "anomaly" event on the timeline.

All detectors are explainable: the event details carry the evidence (MACs, addresses, rates)
and a short hint about what usually causes it on a home network.
"""

from __future__ import annotations

import ipaddress
import logging
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from ..events import ANOMALY, EventLog
from .classify import vendor_for
from .packets import Observation

log = logging.getLogger(__name__)

DUPLICATE_WINDOW_MS = 60_000
DHCP_SERVER_WINDOW_MS = 24 * 3600_000
DHCP_JOIN_WINDOW_MS = 3600_000
DHCP_JOIN_THRESHOLD = 4  # broadcast DHCP exchanges per device per hour
IP_CHANGE_WINDOW_MS = 24 * 3600_000
IP_CHANGE_THRESHOLD = 3
STORM_WINDOW_S = 10
STORM_QUIET_S = 30
COOLDOWN_MS = 3600_000
MAX_PER_TYPE_PER_HOUR = 10  # beyond this, findings of one type are dropped (flood guard)
PRUNE_EVERY_MS = 60_000
GATEWAY_KEY = "discovery_gateway"

HINTS = {
    "mac_flood": "Many never-seen hardware addresses appeared at once. Something on the "
    "network is sending packets with made-up addresses (a misbehaving or compromised device, "
    "or a MAC-flooding tool). NetWatch stopped recording new devices for the moment; look for "
    "the culprit by unplugging or disconnecting devices until it stops.",
    "duplicate_ip": "Two devices answer for the same address. Usually a static IP inside the "
    "DHCP range, or a device that kept an old lease. Traffic to that address flips between them.",
    "dhcp_server": "More than one DHCP server is handing out addresses. With Decos in AP mode "
    "only the Home Hub should answer; a Deco that fell back to router mode is the usual cause.",
    "arp_storm": "An unusually high ARP rate. Common causes: a switching loop (cable plugged in "
    "twice, or a mesh node wired and wireless at once), or a misbehaving device.",
    "gateway_mac": "The gateway's hardware address changed. Expected after replacing the Home "
    "Hub; otherwise it can mean another device is answering for the gateway's address.",
    "dhcp_churn": "The device keeps rejoining the network (repeated DHCP). On Wi-Fi this usually "
    "means it is dropping off and reconnecting, often while roaming between Decos.",
    "ip_churn": "The device's address keeps changing, so anything pointing at it breaks. Give it "
    "a DHCP reservation in the Home Hub.",
    "double_nat": "Traffic passes through two routers doing NAT. Usually a Deco or other router "
    "in router mode behind the Home Hub.",
    "cgnat": "The ISP puts this connection behind carrier-grade NAT (100.64.0.0/10). Inbound "
    "connections and some games/VPNs will not work.",
    "dns_mismatch": "Resolvers disagree about the same name. One of them may be filtering, "
    "hijacking or failing.",
    "nxdomain_rewrite": "This resolver answers for names that do not exist (typo redirection), "
    "which can break software that relies on NXDOMAIN.",
}


@dataclass
class Finding:
    type: str
    key: str
    severity: str
    title: str
    details: dict


def _cgnat(ip: str) -> bool:
    return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")


def _home_private(ip: str) -> bool:
    """Private ranges home routers use for their LAN side. 10/8 is left out on purpose: ISPs,
    Bell included, use it inside their own network, so a 10.x hop is not evidence of NAT."""
    a = ipaddress.ip_address(ip)
    return any(a in ipaddress.ip_network(n) for n in ("192.168.0.0/16", "172.16.0.0/12"))


class AnomalyDetector:
    def __init__(
        self,
        events: EventLog,
        *,
        gateway_ip: Callable[[], str | None] = lambda: None,
        own_macs: Callable[[], set[str]] = set,
        kv_get: Callable[[str, object], object] = lambda k, d=None: d,
        kv_set: Callable[[str, object], None] = lambda k, v: None,
        storm_pps: int = 100,
    ) -> None:
        self.events = events
        self.gateway_ip = gateway_ip
        self.own_macs = own_macs
        self.kv_get, self.kv_set = kv_get, kv_set
        self.storm_pps = storm_pps
        self._claims: dict[str, dict[str, int]] = defaultdict(dict)  # ip → mac → last ts
        self._servers: dict[str, dict] = {}  # DHCP server ip → {mac, first, last}
        self._joins: dict[str, deque[int]] = defaultdict(deque)
        self._ip_changes: dict[str, deque[int]] = defaultdict(deque)
        self._arp_secs: dict[int, int] = defaultdict(int)
        self._arp_by_mac: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._storm_event: int | None = None
        self._storm_quiet_since: int | None = None
        self._storm_peak = 0.0
        self._last_emit: dict[str, int] = {}
        self._type_emits: dict[str, deque[int]] = defaultdict(deque)
        self._gateway: tuple[str, str] | None = None  # (ip, mac), cached from kv
        self._last_prune_ms = 0
        self.suppress_storm_until_ms = 0  # set while NetWatch's own ARP sweep runs
        self.findings: deque[Finding] = deque(maxlen=50)

    # ── emit ─────────────────────────────────────────────────────────────────

    def _emit(self, f: Finding, ts_ms: int, cooldown_ms: int = COOLDOWN_MS) -> bool:
        key = f"{f.type}:{f.key}"
        if ts_ms - self._last_emit.get(key, -(10**15)) < cooldown_ms:
            return False
        # Keys can be attacker-chosen (spoofed addresses), so cap each type as a whole too.
        recent = self._type_emits[f.type]
        while recent and recent[0] <= ts_ms - 3600_000:
            recent.popleft()
        if len(recent) >= MAX_PER_TYPE_PER_HOUR:
            return False
        recent.append(ts_ms)
        self._last_emit[key] = ts_ms
        self.findings.append(f)
        self.events.instant(
            ANOMALY,
            f.severity,
            ts_ms,
            f.title,
            details={"type": f.type, **f.details, "hint": HINTS.get(f.type, "")},
        )
        log.warning("network anomaly", extra={"anomaly": f.type, "title": f.title})
        return True

    # ── packet-driven detectors ──────────────────────────────────────────────

    def observe(self, obs: Observation) -> None:
        if obs.outgoing:
            return
        if obs.proto == "arp":
            sec = obs.ts_ms // 1000
            self._arp_secs[sec] += 1
            self._arp_by_mac[sec][obs.mac] += 1
        if obs.ip and obs.proto in ("arp", "neigh"):
            self._check_claim(obs)
            self._check_gateway(obs)
        if obs.proto == "dhcp":
            self._check_dhcp(obs)

    def _check_claim(self, obs: Observation) -> None:
        claims = self._claims[obs.ip]
        claims[obs.mac] = obs.ts_ms
        recent = {m: t for m, t in claims.items() if obs.ts_ms - t <= DUPLICATE_WINDOW_MS}
        self._claims[obs.ip] = recent
        if len(recent) < 2:
            return
        macs = sorted(recent)
        self._emit(
            Finding(
                "duplicate_ip",
                obs.ip,
                "critical",
                f"Duplicate IP {obs.ip}: {len(macs)} devices claim it",
                {
                    "ip": obs.ip,
                    "macs": macs,
                    "vendors": {m: vendor_for(m) for m in macs},
                    "window_s": DUPLICATE_WINDOW_MS // 1000,
                },
            ),
            obs.ts_ms,
        )

    def _check_gateway(self, obs: Observation) -> None:
        gw = self.gateway_ip()
        if not gw or obs.ip != gw:
            return
        # Remembered per gateway address: a new router on a new address is a fresh baseline.
        # Cached in memory: this runs for every ARP packet from the gateway.
        if self._gateway is None or self._gateway[0] != gw:
            stored = self.kv_get(GATEWAY_KEY, None)
            if isinstance(stored, dict) and stored.get("ip") == gw and stored.get("mac"):
                self._gateway = (gw, stored["mac"])
            else:
                self._gateway = (gw, obs.mac)
                self.kv_set(GATEWAY_KEY, {"ip": gw, "mac": obs.mac})
                return
        known = self._gateway[1]
        if obs.mac == known:
            return
        self._gateway = (gw, obs.mac)
        self._emit(
            Finding(
                "gateway_mac",
                gw,  # per gateway, not per address pair: spoofed addresses can't multiply it
                "critical",
                f"Gateway {gw} changed hardware address",
                {
                    "ip": gw,
                    "previous_mac": known,
                    "new_mac": obs.mac,
                    "previous_vendor": vendor_for(known),
                    "new_vendor": vendor_for(obs.mac),
                },
            ),
            obs.ts_ms,
        )
        self.kv_set(GATEWAY_KEY, {"ip": gw, "mac": obs.mac})

    def _check_dhcp(self, obs: Observation) -> None:
        info = obs.info
        ts = obs.ts_ms
        if info.get("op") == 2 and info.get("type") in ("offer", "ack", "nak"):
            server = info.get("server_ip")
            if not server:
                return
            s = self._servers.setdefault(server, {"mac": obs.mac, "first": ts})
            s["last"], s["mac"] = ts, obs.mac
            live = {
                ip: v for ip, v in self._servers.items() if ts - v["last"] <= DHCP_SERVER_WINDOW_MS
            }
            self._servers = live
            if len(live) > 1:
                servers = [
                    {"ip": ip, "mac": v["mac"], "vendor": vendor_for(v["mac"]),
                     "last_seen_ms": v["last"]}
                    for ip, v in sorted(live.items())
                ]  # fmt: skip
                self._emit(
                    Finding(
                        "dhcp_server",
                        ",".join(sorted(live)),
                        "critical",
                        f"{len(live)} DHCP servers on the network",
                        {"servers": servers},
                    ),
                    ts,
                )
        elif info.get("op") == 1 and info.get("type") in ("discover", "request"):
            # Renewals are unicast to the server and never seen here; a broadcast DISCOVER or
            # REQUEST means the device (re)joined the network. Count exchanges, not packets.
            q = self._joins[obs.mac]
            if q and ts - q[-1] < 30_000:
                return
            q.append(ts)
            while q and ts - q[0] > DHCP_JOIN_WINDOW_MS:
                q.popleft()
            if len(q) >= DHCP_JOIN_THRESHOLD:
                name = info.get("hostname") or obs.mac
                self._emit(
                    Finding(
                        "dhcp_churn",
                        obs.mac,
                        "warning",
                        f"{name} rejoined the network {len(q)} times in an hour",
                        {"mac": obs.mac, "hostname": info.get("hostname"), "joins": len(q)},
                    ),
                    ts,
                )

    def ip_changed(self, mac: str, label: str, old_ip: str, new_ip: str, ts_ms: int) -> None:
        q = self._ip_changes[mac]
        q.append(ts_ms)
        while q and ts_ms - q[0] > IP_CHANGE_WINDOW_MS:
            q.popleft()
        if len(q) >= IP_CHANGE_THRESHOLD:
            self._emit(
                Finding(
                    "ip_churn",
                    mac,
                    "warning",
                    f"{label} changed IP address {len(q)} times in 24 h",
                    {"mac": mac, "from_ip": old_ip, "to_ip": new_ip, "changes": len(q)},
                ),
                ts_ms,
                cooldown_ms=IP_CHANGE_WINDOW_MS,
            )

    # ── periodic: ARP storm open/close ───────────────────────────────────────

    def mac_flood(self, dropped: int, now_ms: int) -> None:
        self._emit(
            Finding(
                "mac_flood",
                "flood",
                "warning",
                f"Flood of new hardware addresses: {dropped} ignored",
                {"ignored": dropped},
            ),
            now_ms,
        )

    def _prune(self, now_ms: int) -> None:
        """Per-address state is keyed by values anyone on the LAN can make up: drop old keys."""
        self._last_prune_ms = now_ms
        for ip in [ip for ip, c in self._claims.items()
                   if not c or now_ms - max(c.values()) > DUPLICATE_WINDOW_MS]:  # fmt: skip
            del self._claims[ip]
        for d, window in (
            (self._joins, DHCP_JOIN_WINDOW_MS),
            (self._ip_changes, IP_CHANGE_WINDOW_MS),
        ):
            for k in [k for k, q in d.items() if not q or now_ms - q[-1] > window]:
                del d[k]
        for k in [k for k, t in self._last_emit.items() if now_ms - t > 24 * 3600_000]:
            del self._last_emit[k]

    def tick(self, now_ms: int) -> None:
        if now_ms - self._last_prune_ms >= PRUNE_EVERY_MS:
            self._prune(now_ms)
        now_s = now_ms // 1000
        for sec in [s for s in self._arp_secs if s < now_s - 60]:
            self._arp_secs.pop(sec, None)
            self._arp_by_mac.pop(sec, None)
        window = range(now_s - STORM_WINDOW_S, now_s)
        total = sum(self._arp_secs.get(s, 0) for s in window)
        pps = total / STORM_WINDOW_S
        if self._storm_event is None:
            if pps >= self.storm_pps and now_ms >= self.suppress_storm_until_ms:
                talkers: dict[str, int] = defaultdict(int)
                for s in window:
                    for mac, n in self._arp_by_mac.get(s, {}).items():
                        talkers[mac] += n
                top = sorted(talkers.items(), key=lambda kv: -kv[1])[:5]
                self._storm_peak = pps
                self._storm_quiet_since = None
                self._storm_event = self.events.open(
                    ANOMALY,
                    "warning",
                    (now_s - STORM_WINDOW_S) * 1000,
                    f"ARP storm: {pps:.0f} packets/s",
                    details={
                        "type": "arp_storm",
                        "pps": round(pps, 1),
                        "threshold_pps": self.storm_pps,
                        "top_senders": [
                            {"mac": m, "vendor": vendor_for(m), "packets": n} for m, n in top
                        ],
                        "hint": HINTS["arp_storm"],
                    },
                )
                self.findings.append(
                    Finding("arp_storm", "storm", "warning", f"ARP storm: {pps:.0f}/s", {})
                )
        else:
            self._storm_peak = max(self._storm_peak, pps)
            if pps < self.storm_pps / 2:
                if self._storm_quiet_since is None:
                    self._storm_quiet_since = now_ms
                elif now_ms - self._storm_quiet_since >= STORM_QUIET_S * 1000:
                    self.events.close(
                        self._storm_event,
                        self._storm_quiet_since,
                        {"peak_pps": round(self._storm_peak, 1)},
                    )
                    self._storm_event = None
            else:
                self._storm_quiet_since = None

    @property
    def arp_pps(self) -> float:
        return (
            0.0
            if not self._arp_secs
            else sum(
                n for s, n in self._arp_secs.items() if s >= max(self._arp_secs) - STORM_WINDOW_S
            )
            / STORM_WINDOW_S
        )

    @property
    def dhcp_servers(self) -> list[dict]:
        return [
            {"ip": ip, "mac": v["mac"], "vendor": vendor_for(v["mac"]), "last_seen_ms": v["last"]}
            for ip, v in sorted(self._servers.items())
        ]

    # ── path tests: double NAT / CGNAT ───────────────────────────────────────

    def check_path(self, test: dict) -> None:
        if test.get("status") != "ok":
            return
        answering = [h["host"] for h in test.get("hops") or [] if h.get("host")]
        if len(answering) < 2:
            return
        try:
            first, second = answering[0], answering[1]
            ipaddress.ip_address(first)
            ipaddress.ip_address(second)
        except ValueError:
            return
        ts = test["ts_ms"]
        if ipaddress.ip_address(first).is_private and _home_private(second) and first != second:
            self._emit(
                Finding(
                    "double_nat",
                    f"{first}>{second}",
                    "warning",
                    f"Double NAT: traffic passes {first} then {second}",
                    {"hops": answering[:4], "host": test.get("host"), "path_test_id": test["id"]},
                ),
                ts,
                cooldown_ms=24 * 3600_000,
            )
        for ip in answering[:4]:
            if _cgnat(ip):
                self._emit(
                    Finding(
                        "cgnat",
                        ip,
                        "info",
                        f"Carrier-grade NAT hop {ip}",
                        {"hops": answering[:4], "path_test_id": test["id"]},
                    ),
                    ts,
                    cooldown_ms=7 * 24 * 3600_000,
                )
                break

    # ── DNS consistency ──────────────────────────────────────────────────────

    def check_dns(self, results: dict, probe_name: str, ts_ms: int) -> list[Finding]:
        """results: {resolver: {name: ("ok", [ips]) | ("nxdomain", []) | ("error", [])}}."""
        found: list[Finding] = []
        names = {n for r in results.values() for n in r}
        for name in sorted(names - {probe_name}):
            per = {res: r[name] for res, r in results.items() if name in r}
            ok = {res: ips for res, (st, ips) in per.items() if st == "ok"}
            nx = [res for res, (st, _) in per.items() if st == "nxdomain"]
            if ok and nx:
                found.append(
                    Finding(
                        "dns_mismatch",
                        f"{name}:nx:{','.join(sorted(nx))}",
                        "warning",
                        f"DNS disagreement for {name}: {', '.join(nx)} says it does not exist",
                        {"name": name, "answers": {k: v for k, v in ok.items()}, "nxdomain": nx},
                    )
                )
            bogus = {
                res: ips
                for res, ips in ok.items()
                if any(ipaddress.ip_address(i).is_private or ipaddress.ip_address(i).is_unspecified
                       for i in ips)
            }  # fmt: skip
            if bogus and len(bogus) < len(ok):
                found.append(
                    Finding(
                        "dns_mismatch",
                        f"{name}:private:{','.join(sorted(bogus))}",
                        "warning",
                        f"DNS disagreement for {name}: {', '.join(bogus)} "
                        "returns a private address",
                        {"name": name, "answers": ok},
                    )
                )
        for res, r in results.items():
            st, ips = r.get(probe_name, ("error", []))
            if st == "ok" and ips:
                found.append(
                    Finding(
                        "nxdomain_rewrite",
                        res,
                        "info",
                        f"Resolver {res} answers for non-existent names",
                        {"resolver": res, "probe": probe_name, "answers": ips},
                    )
                )
        for f in found:
            self._emit(f, ts_ms, cooldown_ms=6 * 3600_000)
        return found
