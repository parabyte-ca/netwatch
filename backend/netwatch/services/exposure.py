"""Exposure scan: which services LAN devices offer, and what the Home Hub lets in from outside.

Two halves:
- a TCP connect scan of a short list of well-known ports on every recently seen LAN device
  (no raw packets, no exploitation; a connection is opened and closed, banners are read only on
  a few text protocols), and
- the Hub's port forwards, UPnP mappings and DMZ host, read through the Hub integration.

Findings combine both: a risky service is worse when the Hub forwards it to the internet.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, delete, desc, insert, select, true
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import device_services, devices, exposure_scans
from .discovery.inventory import device_label
from .events import EXPOSURE, EventLog

log = logging.getLogger(__name__)

LAST_RUN_KEY = "exposure_last_scheduled"
SEEN_WITHIN_MS = 60 * 60_000  # scan devices seen in the last hour
MAX_HOSTS = 254
CONCURRENCY = 64
CONNECT_TIMEOUT_S = 0.8
BANNER_PORTS = frozenset({21, 22, 23, 25, 110, 143})

# port: (service, risk, advice). risk: high = should not be reachable without a good reason,
# medium = fine on the LAN, dangerous if forwarded, info = expected.
PORTS: dict[int, tuple[str, str, str]] = {
    21: ("FTP", "high", "Sends passwords in clear text. Use SFTP, or turn it off."),
    22: ("SSH", "medium", "Fine on the LAN with keys; never forward it without key-only logins."),
    23: ("Telnet", "high", "Unencrypted remote login, a favourite of IoT malware. Turn it off."),
    25: ("SMTP", "medium", "A mail server; unusual on a home device."),
    53: ("DNS", "info", "A DNS server (expected on the Hub and Pi-hole style devices)."),
    80: ("HTTP", "info", "A web interface. Check it has a password."),
    110: ("POP3", "medium", "Unencrypted mail access."),
    139: ("NetBIOS", "medium", "Old Windows file sharing. Keep it off the internet."),
    143: ("IMAP", "medium", "Unencrypted mail access."),
    443: ("HTTPS", "info", "An encrypted web interface."),
    445: ("SMB", "high", "Windows/NAS file sharing. Never expose it; keep it patched on the LAN."),
    554: ("RTSP", "medium", "Camera video stream; often has no password."),
    1883: ("MQTT", "medium", "IoT message broker; often has no password."),
    2375: ("Docker API", "high", "Unauthenticated Docker control = full control of the host."),
    3306: ("MySQL", "medium", "A database. Should only listen where it is needed."),
    3389: ("RDP", "high", "Windows remote desktop. A top target when forwarded; use a VPN."),
    5000: ("UPnP/HTTP", "info", "Often a NAS or media server web interface."),
    5432: ("PostgreSQL", "medium", "A database. Should only listen where it is needed."),
    5900: ("VNC", "high", "Remote desktop, often with weak or no password."),
    6379: ("Redis", "high", "Usually without a password; can be abused to take over the host."),
    8080: ("HTTP (alt)", "info", "A web interface. Check it has a password."),
    8123: ("Home Assistant", "info", "Home Assistant's web interface."),
    8443: ("HTTPS (alt)", "info", "An encrypted web interface."),
    9100: ("Printer (JetDirect)", "medium", "Raw printing; anyone on the LAN can print."),
    11211: ("Memcached", "high", "No authentication; abused for attacks when exposed."),
    27017: ("MongoDB", "high", "Often without a password."),
    32400: ("Plex", "info", "Plex Media Server."),
}
RISK_ORDER = {"info": 0, "medium": 1, "high": 2}
# Worth a warning even when only the LAN can reach them (no authentication by default).
LAN_ALERT = frozenset({23, 2375, 6379, 11211, 27017})


def describe_port(port: int) -> dict:
    name, risk, advice = PORTS.get(port, (f"Port {port}", "info", ""))
    return {"service": name, "risk": risk, "advice": advice}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _private(ip: str | None) -> bool:
    try:
        a = ipaddress.IPv4Address(ip or "")
    except ValueError:
        return False
    return a.is_private and not (a.is_loopback or a.is_link_local or a.is_multicast)


async def tcp_open(ip: str, port: int) -> tuple[bool, str]:
    """(open, banner). A banner is read only from a few text protocols that greet first."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), CONNECT_TIMEOUT_S
        )
    except (OSError, TimeoutError):
        return False, ""
    banner = ""
    try:
        if port in BANNER_PORTS:
            with contextlib.suppress(OSError, TimeoutError):
                raw = await asyncio.wait_for(reader.read(120), 0.6)
                banner = "".join(c for c in raw.decode("latin-1") if c.isprintable()).strip()
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 0.5)
    return True, banner[:120]


Probe = Callable[[str, int], Any]  # async (ip, port) -> (open, banner)


class ExposureStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def add_scan(self, row: dict) -> int:
        values = {**row, "details": json.dumps(row.get("details") or {})}
        with self.engine.begin() as conn:
            return conn.execute(insert(exposure_scans).values(**values)).inserted_primary_key[0]

    def scans(self, limit: int = 20) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(exposure_scans).order_by(desc(exposure_scans.c.ts_ms)).limit(limit)
            ).all()
        out = []
        for r in rows:
            d = dict(r._mapping)
            d["details"] = json.loads(d["details"] or "{}")
            out.append(d)
        return out

    def latest(self) -> dict | None:
        s = self.scans(1)
        return s[0] if s else None

    def services(self, device_id: int | None = None) -> list[dict]:
        stmt = select(device_services)
        if device_id is not None:
            stmt = stmt.where(device_services.c.device_id == device_id)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt.order_by(device_services.c.port))]

    def record(self, found: dict[int, dict[int, str]], scanned: set[int], now_ms: int) -> None:
        """Store this scan's open ports; ports no longer open on scanned devices are dropped."""
        with self.engine.begin() as conn:
            for dev_id, ports in found.items():
                for port, banner in ports.items():
                    stmt = sqlite_insert(device_services).values(
                        device_id=dev_id,
                        port=port,
                        first_seen_ms=now_ms,
                        last_seen_ms=now_ms,
                        banner=banner or None,
                    )
                    conn.execute(
                        stmt.on_conflict_do_update(
                            index_elements=["device_id", "port"],
                            set_={"last_seen_ms": now_ms, "banner": stmt.excluded.banner},
                        )
                    )
            for dev_id in scanned:
                keep = list(found.get(dev_id, {}))
                conn.execute(
                    delete(device_services).where(
                        device_services.c.device_id == dev_id,
                        device_services.c.port.not_in(keep) if keep else true(),
                    )
                )

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            n = conn.execute(delete(exposure_scans).where(exposure_scans.c.ts_ms < cutoff_ms))
            conn.execute(delete(device_services).where(device_services.c.last_seen_ms < cutoff_ms))
            return n.rowcount


def _hosts(engine: Engine, now_ms: int, within_ms: int | None = SEEN_WITHIN_MS) -> dict[str, dict]:
    """LAN devices by IP, most recently seen first: {ip: row of the primary (merged) device}.
    ``within_ms`` limits it to devices seen that recently (None = any)."""
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(select(devices))]
    by_id = {r["id"]: r for r in rows}
    out: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["last_seen_ms"], reverse=True):
        ip = r.get("last_ip")
        if ip in out or not _private(ip):
            continue
        if within_ms is not None and now_ms - r["last_seen_ms"] > within_ms:
            continue
        primary = by_id.get(r["merged_into"]) or r
        primary = {**primary, "details": json.loads(primary.get("details") or "{}")}
        out[ip] = primary
        if within_ms is not None and len(out) >= MAX_HOSTS:
            break
    return out


def findings(services: list[dict], forwards: list[dict], hub: dict | None) -> list[dict]:
    """What deserves attention, worst first."""
    out: list[dict] = []
    if hub and hub.get("dmz_host"):
        out.append(
            {
                "severity": "critical",
                "title": f"DMZ is on: every port of {hub['dmz_host']} is open to the internet",
                "advice": "Turn the DMZ off on the Home Hub and forward only the ports you need.",
                "ip": hub["dmz_host"],
            }
        )
    for f in forwards:
        if not f.get("enabled"):
            continue
        name, risk, advice = PORTS.get(f.get("internal_port") or 0, (None, "medium", ""))
        port = f.get("external_port")
        who = f.get("device_label") or f.get("internal_ip") or "?"
        via = " (opened by the device through UPnP)" if f.get("origin") == "upnp" else ""
        sev = "critical" if risk == "high" else "warning"
        out.append(
            {
                "severity": sev,
                "title": f"Port {port}/{f.get('protocol', 'TCP').lower()} is forwarded to "
                f"{who}{' — ' + name if name else ''}{via}",
                "advice": advice or "Check this is still needed; remove it on the Home Hub if not.",
                "device_id": f.get("device_id"),
                "ip": f.get("internal_ip"),
                "port": f.get("internal_port"),
            }
        )
    if hub and hub.get("upnp_enabled") and any(f.get("origin") == "upnp" for f in forwards):
        out.append(
            {
                "severity": "info",
                "title": "UPnP is on and devices have used it to open ports",
                "advice": "Consoles need it; if nothing here should be reachable, turn UPnP off.",
            }
        )
    for s in services:
        if s["port"] not in LAN_ALERT:
            continue
        out.append(
            {
                "severity": "warning",
                "title": f"{s['service']} ({s['port']}) is open on {s['device_label']}",
                "advice": s["advice"],
                "device_id": s["device_id"],
                "ip": s.get("ip"),
                "port": s["port"],
            }
        )
    rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda f: rank[f["severity"]])


class ExposureScanner:
    def __init__(
        self,
        store: ExposureStore,
        engine: Engine,
        events: EventLog,
        *,
        hub: Any = None,
        probe: Probe | None = None,
        ports: list[int] | None = None,
    ) -> None:
        self.store = store
        self.engine = engine
        self.events = events
        self.hub = hub
        self.probe = probe or tcp_open
        self.ports = ports or sorted(PORTS)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._manual: asyncio.Task | None = None
        self.progress: dict | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def run(self, trigger: str = "manual") -> dict:
        if self._lock.locked():
            raise RuntimeError("a scan is already running")
        async with self._lock:
            try:
                return await self._run(trigger)
            finally:
                self.progress = None

    async def _run(self, trigger: str) -> dict:
        now = _now_ms()
        hosts = await asyncio.to_thread(_hosts, self.engine, now)
        before = {(s["device_id"], s["port"]) for s in await asyncio.to_thread(self.store.services)}
        first_scan = await asyncio.to_thread(self.store.latest) is None
        sem = asyncio.Semaphore(CONCURRENCY)
        total = len(hosts) * len(self.ports)
        self.progress = {"done": 0, "total": total}

        async def one(ip: str, port: int) -> tuple[str, int, bool, str]:
            async with sem:
                ok, banner = await self.probe(ip, port)
            self.progress["done"] += 1
            return ip, port, ok, banner

        results = await asyncio.gather(*(one(ip, p) for ip in hosts for p in self.ports))
        found: dict[int, dict[int, str]] = {}
        for ip, port, ok, banner in results:
            if ok:
                found.setdefault(hosts[ip]["id"], {})[port] = banner
        scanned = {d["id"] for d in hosts.values()}
        await asyncio.to_thread(self.store.record, found, scanned, now)

        hub = await self.hub.exposure() if self.hub is not None else None
        forwards = await asyncio.to_thread(self._forwards, hub)
        prev = await asyncio.to_thread(self.store.latest)
        prev_keys = {_fkey(f) for f in ((prev or {}).get("details", {}).get("forwards") or [])}
        open_ports = sum(len(p) for p in found.values())
        details = {
            "ports_checked": self.ports,
            "hub": {k: v for k, v in (hub or {}).items() if k != "port_mappings"} or None,
            "hub_read": hub is not None,
            "forwards": forwards,
        }
        await asyncio.to_thread(
            self.store.add_scan,
            {
                "ts_ms": now,
                "trigger": trigger,
                "status": "ok",
                "hosts": len(hosts),
                "open_ports": open_ports,
                "details": details,
            },
        )
        if not first_scan:
            await asyncio.to_thread(self._changes, found, before, hosts, forwards, prev_keys, now)
        log.info(
            "exposure scan done",
            extra={"hosts": len(hosts), "open_ports": open_ports, "forwards": len(forwards)},
        )
        return await asyncio.to_thread(self.summary)

    def _forwards(self, hub: dict | None) -> list[dict]:
        maps = (hub or {}).get("port_mappings") or []
        by_ip = _hosts(self.engine, _now_ms(), within_ms=None)
        out = []
        for m in maps:
            dev = by_ip.get(m.get("internal_ip") or "")
            out.append(
                {
                    **m,
                    "device_id": dev["id"] if dev else None,
                    "device_label": device_label(dev) if dev else None,
                }
            )
        return out

    def _changes(self, found, before, hosts, forwards, prev_keys, now) -> None:
        labels = {d["id"]: device_label(d) for d in hosts.values()}
        for dev_id, ports in found.items():
            for port in ports:
                if (dev_id, port) in before:
                    continue
                name, risk, advice = PORTS.get(port, (str(port), "info", ""))
                if risk == "info":
                    continue
                self.events.instant(
                    EXPOSURE,
                    "warning" if port in LAN_ALERT else "info",
                    now,
                    f"New service on {labels.get(dev_id, '?')}: {name} ({port})",
                    details={"type": "new_service", "device_id": dev_id, "port": port,
                             "advice": advice},
                )  # fmt: skip
        for f in forwards:
            if not f.get("enabled") or _fkey(f) in prev_keys:
                continue
            via = "through UPnP" if f.get("origin") == "upnp" else "on the Hub"
            self.events.instant(
                EXPOSURE,
                "warning",
                now,
                f"Port {f.get('external_port')} opened to the internet {via} for "
                f"{f.get('device_label') or f.get('internal_ip')}",
                details={"type": "new_forward", "forward": f, "device_id": f.get("device_id")},
            )

    def summary(self) -> dict:
        """The latest scan, open services by device and the findings, for the API."""
        latest = self.store.latest()
        rows = self.store.services()
        ids = {r["device_id"] for r in rows}
        with self.engine.connect() as conn:
            devs = {
                r.id: {**dict(r._mapping), "details": json.loads(r.details or "{}")}
                for r in conn.execute(select(devices).where(devices.c.id.in_(ids)))
            } if ids else {}  # fmt: skip
        services = []
        for r in rows:
            name, risk, advice = PORTS.get(r["port"], (f"Port {r['port']}", "info", ""))
            d = devs.get(r["device_id"]) or {}
            services.append(
                {
                    **r,
                    "service": name,
                    "risk": risk,
                    "advice": advice,
                    "device_label": device_label(d) if d else "?",
                    "ip": d.get("last_ip"),
                }
            )
        services.sort(key=lambda s: (-RISK_ORDER[s["risk"]], s["device_label"], s["port"]))
        det = (latest or {}).get("details") or {}
        forwards = det.get("forwards") or []
        return {
            "latest": latest,
            "running": self.running,
            "progress": self.progress,
            "services": services,
            "forwards": forwards,
            "hub": det.get("hub"),
            "hub_read": det.get("hub_read", False),
            "findings": findings(services, forwards, det.get("hub")),
        }

    # ── scheduling ───────────────────────────────────────────────────────────

    def start_now(self) -> bool:
        if self._lock.locked():
            return False
        self._manual = asyncio.create_task(self.run("manual"), name="netwatch-exposure-run")
        return True

    def start_schedule(
        self,
        *,
        enabled: Callable[[], bool],
        window: Callable[[], str],
        tz: Callable[[], str],
        kv_get: Callable,
        kv_set: Callable,
    ) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    if not enabled():
                        continue
                    now = datetime.now(ZoneInfo(tz()))
                    h, m = (int(x) for x in (await asyncio.to_thread(window)).split(":"))
                    # 40 minutes after the update window (the bufferbloat test runs at +20).
                    start = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(
                        minutes=40
                    )
                    today = now.date().isoformat()
                    last = await asyncio.to_thread(kv_get, LAST_RUN_KEY, None)
                    if start <= now < start + timedelta(hours=2) and last != today:
                        await asyncio.to_thread(kv_set, LAST_RUN_KEY, today)
                        await self.run("scheduled")
                except Exception:
                    log.exception("scheduled exposure scan failed")

        self._task = asyncio.create_task(loop(), name="netwatch-exposure")

    async def stop(self) -> None:
        for t in (self._task, self._manual):
            if t and not t.done():
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        self._task = self._manual = None


def _fkey(f: dict) -> str:
    return "{}:{}>{}:{}".format(
        f.get("protocol"), f.get("external_port"), f.get("internal_ip"), f.get("internal_port")
    )
