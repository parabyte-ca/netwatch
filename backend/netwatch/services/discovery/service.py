"""Discovery service: wires capture, inventory, anomaly detection and opt-in active scans."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import secrets
import time
from collections import Counter
from collections.abc import Awaitable, Callable

import dns.asyncquery
import dns.exception
import dns.message
import dns.rcode
import dns.rdatatype
import dns.reversename

from ...config import Settings
from ..events import DEVICE, EventLog
from ..netinfo import default_route
from ..storage import Store
from .anomalies import GATEWAY_KEY, AnomalyDetector
from .capture import (
    FrameQueue,
    MulticastMembership,
    Sender,
    Sniffer,
    iface_ipv4,
    iface_mac,
    scan_networks,
    send_udp,
)
from .inventory import Change, DeviceStore, Inventory, device_label
from .packets import (
    SSDP_SEARCH,
    Observation,
    arp_request,
    dhcp_discover,
    mdns_browse_query,
    parse_frame,
    parse_proc_arp,
    random_private_mac,
)

log = logging.getLogger(__name__)

ACTIVE_KEY = "discovery_active"
LEARNING_KEY = "discovery_learning_until_ms"
PROBE_MAC_KEY = "discovery_probe_mac"
LEARNING_MS = 3600_000  # no "new device" events during the first hour (initial inventory)
NEIGH_EVERY_MS = 30_000
DNS_NAMES = ("www.google.com", "www.cloudflare.com", "www.bell.ca", "github.com")
FALLBACK_RESOLVERS = ("1.1.1.1", "8.8.8.8")

DnsQuery = Callable[[str, str, str], Awaitable[tuple[str, list[str]]]]
TargetsChanged = Callable[[], Awaitable[None]]


async def dns_lookup(resolver: str, name: str, rdtype: str = "A") -> tuple[str, list[str]]:
    q = dns.message.make_query(name, dns.rdatatype.from_text(rdtype))
    try:
        r = await dns.asyncquery.udp(q, resolver, timeout=3.0)
    except (dns.exception.DNSException, OSError):
        return "error", []
    if r.rcode() == dns.rcode.NXDOMAIN:
        return "nxdomain", []
    if r.rcode() != dns.rcode.NOERROR:
        return "error", []
    out: list[str] = []
    for rr in r.answer:
        for item in rr:
            if rr.rdtype == dns.rdatatype.A:
                out.append(item.address)
            elif rr.rdtype == dns.rdatatype.PTR:
                out.append(item.target.to_text().rstrip("."))
    return "ok", sorted(set(out))


class DiscoveryService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        events: EventLog,
        devices: DeviceStore,
        *,
        on_targets_changed: TargetsChanged | None = None,
        dns_query: DnsQuery | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.devices = devices
        self.on_targets_changed = on_targets_changed
        self.dns_query = dns_query or (lambda res, name, t: dns_lookup(res, name, t))
        self.proc_root = settings.proc_root
        self.queue = FrameQueue()
        self.sniffer: Sniffer | None = None
        self.multicast = MulticastMembership()
        self.iface: str | None = None
        self.local: ipaddress.IPv4Interface | None = None
        self._own_macs: tuple[float, set[str]] = (0.0, set())
        self._gateway: tuple[float, str | None] = (0.0, None)
        self.inventory = Inventory(devices, own_macs=self.own_macs, gateway_ip=self.gateway_ip)
        self.anomalies = AnomalyDetector(
            events,
            gateway_ip=self.gateway_ip,
            own_macs=self.own_macs,
            kv_get=store.kv_get,
            kv_set=store.kv_set,
            storm_pps=settings.arp_storm_pps,
        )
        self.counts: Counter[str] = Counter()
        self._last_neigh_ms = 0
        self._tasks: list[asyncio.Task] = []
        self._scan_lock = asyncio.Lock()
        self.last_scan: dict | None = None
        self.last_dns_check: dict | None = None
        self.started_at: float | None = None

    # ── host facts (cached) ──────────────────────────────────────────────────

    def gateway_ip(self) -> str | None:
        ts, gw = self._gateway
        if time.monotonic() - ts > 30:
            route = default_route(self.proc_root)
            gw = route.gateway if route else None
            self._gateway = (time.monotonic(), gw)
        return gw

    def own_macs(self) -> set[str]:
        ts, macs = self._own_macs
        if time.monotonic() - ts > 300:
            net = self.settings.sysfs_root / "class" / "net"
            found = set()
            with contextlib.suppress(OSError):
                for p in net.iterdir():
                    mac = iface_mac(self.settings.sysfs_root, p.name)
                    if mac and mac != "00:00:00:00:00:00":
                        found.add(mac)
            macs = found
            self._own_macs = (time.monotonic(), macs)
        return macs

    # ── settings ─────────────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return bool(self.store.kv_get(ACTIVE_KEY, self.settings.discovery_active))

    def set_active(self, enabled: bool) -> None:
        self.store.kv_set(ACTIVE_KEY, bool(enabled))

    def _probe_mac(self) -> str:
        """One made-up client address for the DHCP probe, kept for good: a fresh one per scan
        would leave a phantom client in the router's list every hour."""
        m = self.store.kv_get(PROBE_MAC_KEY, None)
        if not isinstance(m, str):
            m = random_private_mac()
            self.store.kv_set(PROBE_MAC_KEY, m)
        return m

    def _on_local_network(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        nets = list(self.networks()[0])
        if self.local:
            nets.append(self.local.network)
        if not nets:  # interface unknown: at least stay on private addresses
            return addr.is_private and not addr.is_loopback and not addr.is_link_local
        return any(addr in n for n in nets)

    def networks(self) -> tuple[list[ipaddress.IPv4Network], list[str]]:
        return scan_networks(self.settings.discovery_subnet_list, self.local)

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return bool(self._tasks) and all(not t.done() for t in self._tasks)

    async def start(self) -> None:
        self.started_at = time.time()
        route = default_route(self.proc_root)
        self.iface = self.settings.discovery_interface or (route.interface if route else None)
        if self.iface:
            self.local = await asyncio.to_thread(iface_ipv4, self.iface)
        now = int(time.time() * 1000)
        if await asyncio.to_thread(self.store.kv_get, LEARNING_KEY, None) is None:
            await asyncio.to_thread(self.store.kv_set, LEARNING_KEY, now + LEARNING_MS)
        if self.settings.discovery_passive and self.iface:
            if self.local:
                self.multicast.join(str(self.local.ip))
            self.sniffer = Sniffer(self.iface, self.queue.put)
            await asyncio.to_thread(self.sniffer.start)
        self._tasks = [asyncio.create_task(self._drain_loop(), name="netwatch-discovery")]
        self._tasks.append(asyncio.create_task(self._scan_loop(), name="netwatch-discovery-scan"))
        if self.settings.dns_check_interval_min > 0:
            self._tasks.append(asyncio.create_task(self._dns_loop(), name="netwatch-dns-check"))
        log.info(
            "discovery started",
            extra={
                "iface": self.iface,
                "capture": bool(self.sniffer and self.sniffer.running),
                "active": self.active,
            },
        )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks = []
        if self.sniffer:
            await asyncio.to_thread(self.sniffer.stop)
        self.multicast.leave()

    # ── processing ───────────────────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                await self.process_pending()
            except Exception:
                log.exception("discovery processing failed")

    async def process_pending(self, now_ms: int | None = None) -> list[Change]:
        frames = self.queue.drain()
        changes = await asyncio.to_thread(self._process, frames, now_ms)
        for ch in changes:
            try:
                await self._handle_change(ch)
            except Exception:
                log.exception("failed to handle device change", extra={"mac": ch.device["mac"]})
        return changes

    def _process(self, frames: list, now_ms: int | None) -> list[Change]:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        obs: list[Observation] = []
        own = self.own_macs()
        for ts, frame, outgoing in frames:
            try:
                parsed = parse_frame(frame, ts, outgoing=outgoing)
            except Exception:  # a frame no parser expected must not drop the whole batch
                self.counts["unparsable"] += 1
                continue
            for o in parsed:
                self.counts[o.proto] += 1
            # What this host sends describes only itself (the DHCP probe carries a made-up
            # client MAC that must not become a device).
            obs.extend(o for o in parsed if not o.outgoing or o.mac in own)
        if now - self._last_neigh_ms >= NEIGH_EVERY_MS:
            self._last_neigh_ms = now
            obs.extend(self.neighbours(now))
        for o in obs:
            self.anomalies.observe(o)
        before = self.inventory.flood_dropped
        changes = self.inventory.apply(obs)
        if self.inventory.flood_dropped > before:
            self.anomalies.mac_flood(self.inventory.flood_dropped - before, now)
        self.anomalies.tick(now)
        return changes

    def neighbours(self, now_ms: int) -> list[Observation]:
        """The host's own ARP cache: cheap, passive, and covers quiet wired devices."""
        try:
            text = (self.proc_root / "net" / "arp").read_text()
        except OSError:
            return []
        return [
            Observation(now_ms, "neigh", mac, ip=ip, info={"dev": dev})
            for ip, mac, dev in parse_proc_arp(text, self.iface)
        ]

    async def _handle_change(self, ch: Change) -> None:
        d = ch.device
        now = int(time.time() * 1000)
        if ch.kind == "new":
            learning_until = await asyncio.to_thread(self.store.kv_get, LEARNING_KEY, 0)
            # Private (randomised) MACs rotate; announcing each one would be noise.
            if now >= (learning_until or 0) and not d.get("randomised"):
                await asyncio.to_thread(
                    self.events.instant,
                    DEVICE,
                    "info",
                    d["first_seen_ms"],
                    f"New device: {device_label(d)}",
                    details={"mac": d["mac"], "vendor": d.get("vendor"), "device_id": d["id"]},
                )
        elif ch.kind == "ip":
            fresh = await asyncio.to_thread(self.devices.get, d["id"])
            if fresh is None:
                return
            label = device_label(fresh)
            new_ip = fresh["last_ip"]
            await asyncio.to_thread(
                self.anomalies.ip_changed, fresh["mac"], label, ch.old_ip or "", new_ip or "", now
            )
            primary = fresh
            if fresh.get("merged_into"):
                primary = await asyncio.to_thread(self.devices.get, fresh["merged_into"]) or fresh
            if primary.get("target_id") and new_ip:
                await self.retarget(primary, new_ip, old_ip=ch.old_ip)

    async def retarget(self, device: dict, new_ip: str, *, old_ip: str | None) -> None:
        """Keep a monitored device's ping target pointing at its current address."""
        target = await asyncio.to_thread(self.store.get_target, device["target_id"])
        if target is None or target["host"] == new_ip:
            return
        if not self._on_local_network(new_ip):
            # A spoofed packet can claim any address for a device; only follow it within the
            # networks NetWatch watches.
            log.warning("not following device to an outside address", extra={"ip": new_ip})
            return
        await asyncio.to_thread(self.store.update_target, target["id"], {"host": new_ip})
        await asyncio.to_thread(
            self.events.instant,
            DEVICE,
            "info",
            int(time.time() * 1000),
            f"{device_label(device)} moved from {old_ip or target['host']} to {new_ip}",
            details={"device_id": device["id"], "target_id": target["id"], "ip": new_ip},
        )
        if self.on_targets_changed:
            await self.on_targets_changed()

    # ── active scanning (opt-in) ─────────────────────────────────────────────

    async def _scan_loop(self) -> None:
        await asyncio.sleep(90)
        while True:
            try:
                last = (self.last_scan or {}).get("finished_ms") or 0
                interval_ms = self.settings.discovery_scan_interval_min * 60_000
                due = time.time() * 1000 - last >= interval_ms
                if self.active and due:
                    await self.scan(trigger="scheduled")
            except Exception:
                log.exception("discovery scan failed")
            await asyncio.sleep(60)

    @property
    def scanning(self) -> bool:
        return self._scan_lock.locked()

    def scan_possible(self) -> str | None:
        """None if a scan can run, else why not."""
        if not self.iface or not self.local:
            return "no IPv4 address on the capture interface"
        if not self.networks()[0]:
            return "no private subnet to scan"
        return None

    async def scan(self, *, trigger: str) -> dict:
        async with self._scan_lock:
            return await self._scan(trigger)

    async def _scan(self, trigger: str) -> dict:
        reason = self.scan_possible()
        started = int(time.time() * 1000)
        result: dict = {"trigger": trigger, "started_ms": started}
        if reason:
            result.update(error=reason, finished_ms=started)
            self.last_scan = result
            return result
        if not self.iface or not self.local:  # for the type checker; scan_possible covers it
            return result
        own_mac = iface_mac(self.settings.sysfs_root, self.iface)
        nets, notes = self.networks()
        hosts = [str(h) for n in nets for h in n.hosts() if h != self.local.ip]
        rate = self.settings.discovery_scan_rate_pps
        # NetWatch's own sweep provokes a burst of replies; don't report it as an ARP storm.
        self.anomalies.suppress_storm_until_ms = started + int(len(hosts) / rate * 1000) + 15_000
        errors: list[str] = []
        if own_mac:
            try:
                await self._arp_sweep(own_mac, str(self.local.ip), hosts, rate)
            except OSError as exc:
                errors.append(f"ARP sweep: {exc}")
            try:
                probe_mac = await asyncio.to_thread(self._probe_mac)
                probe = dhcp_discover(own_mac, secrets.randbits(32), probe_mac)
                await asyncio.to_thread(self._send_frames, [probe])
            except OSError as exc:
                errors.append(f"DHCP probe: {exc}")
        for payload, group, port in (
            (SSDP_SEARCH, "239.255.255.250", 1900),
            (mdns_browse_query(), "224.0.0.251", 5353),
        ):
            try:
                await asyncio.to_thread(send_udp, payload, group, port, str(self.local.ip), 3.0)
            except OSError as exc:
                errors.append(f"{port}: {exc}")
        await asyncio.sleep(1.0)
        await self.process_pending()
        named = await self._reverse_dns()
        result.update(
            finished_ms=int(time.time() * 1000),
            networks=[str(n) for n in nets],
            hosts=len(hosts),
            notes=notes,
            errors=errors,
            reverse_dns_names=named,
        )
        self.last_scan = result
        log.info("discovery scan finished", extra={k: v for k, v in result.items() if k != "notes"})
        return result

    async def _arp_sweep(self, own_mac: str, own_ip: str, hosts: list[str], rate: int) -> None:
        batch = max(1, rate // 10)
        for i in range(0, len(hosts), batch):
            frames = [arp_request(own_mac, own_ip, h) for h in hosts[i : i + batch]]
            await asyncio.to_thread(self._send_frames, frames)
            await asyncio.sleep(len(frames) / rate)

    def _send_frames(self, frames: list[bytes]) -> None:
        with Sender(self.iface or "") as s:
            for f in frames:
                s.send(f)

    async def _reverse_dns(self, limit: int = 64) -> int:
        """Ask the gateway's resolver (it knows its DHCP clients) for names of unnamed devices."""
        gw = self.gateway_ip()
        if not gw:
            return 0
        now = int(time.time() * 1000)
        rows = await asyncio.to_thread(self.devices.all)
        todo = [
            r for r in rows
            if r["last_ip"] and not (r["hostname"] or r["mdns_name"] or r["details"].get("ptr"))
            and now - r["last_seen_ms"] < 86_400_000
        ][:limit]  # fmt: skip
        found: list[Observation] = []
        sem = asyncio.Semaphore(8)  # a dead resolver must not hold the scan for minutes

        async def lookup(r: dict) -> None:
            async with sem:
                status, names = await self.dns_query(
                    gw, dns.reversename.from_address(r["last_ip"]).to_text(), "PTR"
                )
            if status == "ok" and names:
                found.append(Observation(now, "rdns", r["mac"], info={"ptr": names[0][:64]}))

        await asyncio.gather(*(lookup(r) for r in todo))
        if found:
            await asyncio.to_thread(self.inventory.apply, found)
        return len(found)

    # ── DNS consistency check ────────────────────────────────────────────────

    async def _dns_loop(self) -> None:
        await asyncio.sleep(120)
        while True:
            try:
                await self.check_dns()
            except Exception:
                log.exception("DNS consistency check failed")
            await asyncio.sleep(self.settings.dns_check_interval_min * 60)

    def resolvers(self) -> list[str]:
        hosts = [
            t["host"] for t in self.store.list_targets(enabled_only=True) if t["kind"] == "dns"
        ]
        if not hosts:
            gw = self.gateway_ip()
            hosts = ([gw] if gw else []) + list(FALLBACK_RESOLVERS)
        return list(dict.fromkeys(hosts))

    async def check_dns(self) -> dict:
        resolvers = await asyncio.to_thread(self.resolvers)
        probe = f"netwatch-{secrets.token_hex(6)}.com"
        names = [*DNS_NAMES, probe]
        results: dict[str, dict] = {}

        async def one(res: str, name: str) -> None:
            results.setdefault(res, {})[name] = await self.dns_query(res, name, "A")

        await asyncio.gather(*(one(r, n) for r in resolvers for n in names))
        now = int(time.time() * 1000)
        findings = await asyncio.to_thread(self.anomalies.check_dns, results, probe, now)
        self.last_dns_check = {
            "ts_ms": now,
            "resolvers": resolvers,
            "findings": [f.title for f in findings],
        }
        return self.last_dns_check

    # ── status for the API ───────────────────────────────────────────────────

    def _gateway_mac(self) -> str | None:
        stored = self.store.kv_get(GATEWAY_KEY, None)
        if isinstance(stored, dict) and stored.get("ip") == self.gateway_ip():
            return stored.get("mac")
        return None

    def status(self) -> dict:
        nets, notes = self.networks()
        sn = self.sniffer
        return {
            "interface": self.iface,
            "address": str(self.local) if self.local else None,
            "passive": {
                "enabled": self.settings.discovery_passive,
                "running": bool(sn and sn.running),
                "error": sn.error if sn else None,
                "frames": sn.frames if sn else 0,
                "dropped": self.queue.dropped,
                "packets": dict(self.counts),
                "arp_pps": round(self.anomalies.arp_pps, 1),
            },
            "active": {
                "enabled": self.active,
                "interval_min": self.settings.discovery_scan_interval_min,
                "rate_pps": self.settings.discovery_scan_rate_pps,
                "networks": [str(n) for n in nets],
                "notes": notes,
                "scanning": self.scanning,
                "unavailable": self.scan_possible(),
                "last_scan": self.last_scan,
            },
            "gateway": {"ip": self.gateway_ip(), "mac": self._gateway_mac()},
            "dhcp_servers": self.anomalies.dhcp_servers,
            "dns_check": self.last_dns_check,
            "learning_until_ms": self.store.kv_get(LEARNING_KEY, None),
        }
