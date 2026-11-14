"""AdGuard Home integration: reads the LAN's DNS resolver every minute (read-only).

Records query volume, block rate, errors, latency and per-upstream health, and turns problems
into "dns" timeline events the diagnosis uses as evidence: "AdGuard Home is unreachable", "an
upstream resolver is failing", "DNS errors spiked".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import statistics
import time
from collections import Counter, deque
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, delete, insert, select

from ...db import adguard_stats, device_ips, devices
from ..discovery.anomalies import HINTS
from ..discovery.inventory import device_label
from ..discovery.packets import Observation
from ..events import ANOMALY, DNS, EventLog
from ..secrets import SecretBox
from ..storage import Store
from .client import AdGuardClient, AdGuardError, AdGuardLoginError, parse_time
from .clients import HOUR_MS, ClientAccumulator, ClientStore, entry_outcome, hour_of

log = logging.getLogger(__name__)

CONFIG_KEY = "adguard_config"
PASSWORD_KEY = "adguard_password"  # noqa: S105 - the kv key, not a password
CURSOR_KEY = "adguard_cursor"
CLIENTS_KEY = "adguard_clients"
POLL_S = 60
MAX_LOGIN_FAILURES = 3
UNREACHABLE_AFTER = 3  # failed polls in a row before "unreachable" opens
# Per-device rate alerts: the last 60 minutes against the device's hours over the last 7 days.
RATE_HISTORY_HOURS = 24  # hours of history a device needs before it can be judged
RATE_MIN_QUERIES, RATE_FACTOR = 500, 5
BLOCKED_SHARE, BLOCKED_MIN_QUERIES, BLOCKED_USUAL_MAX = 0.5, 100, 0.15
RATE_COOLDOWN_MS = 6 * 3_600_000
RATE_MAX_PER_HOUR = 10  # per alert type, over all devices
INVENTORY_EVERY = 5  # polls between reading AdGuard's clients and DHCP leases into the inventory
QUERYLOG_LIMIT = 2000  # entries read per poll at most
CATCH_UP_MS = 10 * 60_000  # after a restart, read at most this far back
UPSTREAM_FAIL_RATE, UPSTREAM_MIN_QUERIES, UPSTREAM_CLEAN_TO_CLOSE = 0.5, 10, 3
ERROR_RATE, ERROR_MIN_QUERIES = 0.05, 20
SPIKE_FLOOR_MS, SPIKE_FACTOR, SPIKE_MIN_HISTORY = 150.0, 3.0, 30
SPIKE_COOLDOWN_MS = ERROR_COOLDOWN_MS = 30 * 60_000
NEW_CLIENTS_PER_HOUR = 10
MAX_KNOWN_CLIENTS = 2000
OK_STATUS = frozenset({"NOERROR", "NXDOMAIN"})

ClientFactory = Callable[[dict, str], AdGuardClient]


def default_factory(cfg: dict, password: str) -> AdGuardClient:
    return AdGuardClient(cfg["host"], cfg["port"], cfg["username"], password, https=cfg["https"])


def interval_metrics(entries: list[dict]) -> dict:
    """Counts, latency and per-upstream health from query-log entries."""
    queries = len(entries)
    blocked = servfail = errors = 0
    times: list[float] = []
    ups: dict[str, dict] = {}
    clients: Counter[str] = Counter()
    for e in entries:
        status = str(e.get("status") or "").upper()
        reason = str(e.get("reason") or "")
        if e.get("client"):
            clients[str(e["client"])[:64]] += 1
        if reason.startswith("Filtered"):
            blocked += 1
            continue
        failed = False
        if status == "SERVFAIL":
            servfail += 1
            failed = True
        elif status and status not in OK_STATUS:
            errors += 1
            failed = True
        try:
            ms = float(e.get("elapsedMs"))
        except (TypeError, ValueError):
            ms = None
        if ms is not None and ms >= 0:
            times.append(ms)
        up = str(e.get("upstream") or "")[:120]
        if up:
            u = ups.setdefault(up, {"n": 0, "fail": 0, "_t": []})
            u["n"] += 1
            u["fail"] += failed
            if ms is not None:
                u["_t"].append(ms)
    for u in ups.values():
        t = u.pop("_t")
        u["avg_ms"] = round(sum(t) / len(t), 1) if t else None
    times.sort()
    return {
        "queries": queries,
        "blocked": blocked,
        "servfail": servfail,
        "errors": errors,
        "avg_ms": round(sum(times) / len(times), 1) if times else None,
        "p95_ms": round(times[min(len(times) - 1, int(len(times) * 0.95))], 1) if times else None,
        "upstreams": ups,
        "top_clients": clients.most_common(5),
    }


def _pairs(items) -> dict:
    """AdGuard's stats lists of one-entry dicts ([{"a": 1}, {"b": 2}]) as one dict."""
    out: dict = {}
    for it in items or []:
        if isinstance(it, dict):
            out.update(it)
    return out


def _row(r) -> dict:
    d = dict(r._mapping)
    d["upstreams"] = json.loads(d.get("upstreams") or "{}")
    d["details"] = json.loads(d.get("details") or "{}")
    return d


class AdGuardStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def add(self, row: dict) -> None:
        values = {
            **row,
            "upstreams": json.dumps(row.get("upstreams") or {}),
            "details": json.dumps(row.get("details") or {}),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(adguard_stats).values(**values))

    def history(self, start_ms: int, end_ms: int, limit: int = 20_000) -> list[dict]:
        stmt = (
            select(adguard_stats)
            .where(adguard_stats.c.ts_ms >= start_ms, adguard_stats.c.ts_ms < end_ms)
            .order_by(adguard_stats.c.ts_ms)
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(stmt)]

    def latest(self) -> dict | None:
        stmt = select(adguard_stats).order_by(adguard_stats.c.ts_ms.desc()).limit(1)
        with self.engine.connect() as conn:
            r = conn.execute(stmt).first()
        return _row(r) if r else None

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(adguard_stats).where(adguard_stats.c.ts_ms < cutoff_ms)
            ).rowcount


class AdGuardService:
    def __init__(
        self,
        store: Store,
        events: EventLog,
        adguard_store: AdGuardStore,
        secrets: SecretBox,
        *,
        client_factory: ClientFactory | None = None,
        clients: ClientStore | None = None,
        inventory=None,
    ) -> None:
        self.store = store
        self.client_store = clients
        self.accumulator = ClientAccumulator(clients) if clients is not None else None
        self.inventory = inventory  # discovery Inventory; AdGuard clients and leases feed it
        self.dhcp: dict | None = None  # {available, enabled, leases} from the last read
        self._polls = 0
        self._recent: deque[tuple[int, dict[int, tuple[int, int]]]] = deque()  # per poll
        self._baseline: dict[int, tuple[float, float, int]] = {}  # median q, usual share, hours
        self._baseline_hour = -1
        self._rate_last: dict[str, int] = {}  # "type:device" → last alert
        self._rate_times: dict[str, deque[int]] = {}
        self.events = events
        self.adguard_store = adguard_store
        self.secrets = secrets
        self.client_factory = client_factory or default_factory
        self.last: dict | None = None  # latest status (version, protection, ...)
        self.last_poll_ms: int | None = None
        self.last_ok_ms: int | None = None
        self.error: str | None = None
        self.paused: str | None = None
        self._failures = 0  # sign-in failures in a row
        self._unreachable = 0  # failed polls in a row
        self._client: AdGuardClient | None = None
        self._client_key: tuple | None = None
        self._task: asyncio.Task | None = None
        self._kick: asyncio.Task | None = None
        self._cursor: datetime | None = None
        self._prev_stats: dict | None = None
        self._open: dict[str, int] = {}  # open event ids: "unreachable", "protection", "up:<x>"
        self._clean: dict[str, int] = {}  # clean intervals per failing upstream
        self._last_error_ms = 0
        self._last_spike_ms = 0
        self._avg_history: deque[float] | None = None
        self._new_client_times: deque[int] = deque()

    # ── configuration ────────────────────────────────────────────────────────

    def config(self) -> dict:
        c = self.store.kv_get(CONFIG_KEY, {}) or {}
        return {
            "host": c.get("host", ""),
            "port": int(c.get("port") or 80),
            "https": bool(c.get("https", False)),
            "username": c.get("username", ""),
            "enabled": bool(c.get("enabled", False)),
        }

    def password(self) -> str | None:
        token = self.store.kv_get(PASSWORD_KEY, None)
        return self.secrets.decrypt(token) if token else None

    @property
    def configured(self) -> bool:
        c = self.config()
        return bool(c["host"] and c["username"]) and (
            self.store.kv_get(PASSWORD_KEY, None) is not None
        )

    def save(
        self,
        host: str,
        port: int,
        https: bool,
        username: str,
        enabled: bool,
        password: str | None,
    ) -> None:
        host, username = host.strip(), username.strip()
        old = self.config()
        if (host, port, https) != (old["host"], old["port"], old["https"]) and not password:
            # The password only ever goes to the address it was saved for.
            self.store.kv_set(PASSWORD_KEY, None)
        self.store.kv_set(
            CONFIG_KEY,
            {"host": host, "port": port, "https": https, "username": username, "enabled": enabled},
        )
        if password:
            self.store.kv_set(PASSWORD_KEY, self.secrets.encrypt(password))
        self._failures = 0
        self.paused = None
        if not enabled:
            self._close_all(int(time.time() * 1000))

    def forget_password(self) -> None:
        self.store.kv_set(PASSWORD_KEY, None)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-adguard")

    def kick(self) -> None:
        if self.config()["enabled"] and self.configured and not self.paused:
            self._kick = asyncio.create_task(self.poll(), name="netwatch-adguard-kick")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._drop()

    async def _drop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None
        self._client_key = None

    async def _loop(self) -> None:
        await asyncio.sleep(15)
        while True:
            try:
                cfg = await asyncio.to_thread(self.config)
                if (
                    cfg["enabled"]
                    and not self.paused
                    and await asyncio.to_thread(lambda: self.configured)
                ):
                    await self.poll()
            except Exception:
                log.exception("AdGuard Home poll failed")
            await asyncio.sleep(POLL_S)

    async def _ensure_client(self) -> AdGuardClient | None:
        cfg = await asyncio.to_thread(self.config)
        pw = await asyncio.to_thread(self.password)
        if not (cfg["host"] and cfg["username"] and pw):
            return None
        key = (cfg["host"], cfg["port"], cfg["https"], cfg["username"], pw)
        if self._client_key != key:
            await self._drop()
            self._client = self.client_factory(cfg, pw)
            self._client_key = key
        return self._client

    # ── polling ──────────────────────────────────────────────────────────────

    async def poll(self, now_ms: int | None = None) -> bool:
        now = now_ms or int(time.time() * 1000)
        self.last_poll_ms = now
        if await asyncio.to_thread(self.password) is None:
            self.error = "the stored password cannot be decrypted; enter it again"
            return False
        client = await self._ensure_client()
        if client is None:
            self.error = "save the address, username and password first"
            return False
        try:
            status = await client.status()
            if await client.querylog_enabled():
                since = await asyncio.to_thread(self._since, now)
                entries, cut = await client.querylog(since, QUERYLOG_LIMIT)
                stats = None
            else:
                entries, cut = [], False
                stats = await client.stats()
            known = await client.clients()
            dhcp = None
            if self.inventory is not None and self._polls % INVENTORY_EVERY == 0:
                dhcp = await client.dhcp_status()
        except AdGuardLoginError as exc:
            self.error = str(exc)
            self._failures += 1
            if self._failures >= MAX_LOGIN_FAILURES:
                self.paused = f"{self._failures} failed sign-ins"
            await self._drop()
            return False
        except AdGuardError as exc:
            self.error = str(exc)
            log.warning("AdGuard Home poll failed", extra={"error": self.error})
            await asyncio.to_thread(self._unreachable_poll, now)
            return False
        self._failures = 0
        self.error = None
        self.last_ok_ms = now
        self.last = status
        self._polls += 1
        await asyncio.to_thread(self._apply, now, status, entries, cut, stats, known)
        if dhcp is not None:
            # Before recording per-client counts, so a device a lease just added is matched.
            self.dhcp = dhcp
            await asyncio.to_thread(self._feed_inventory, now, known, dhcp)
        if self.accumulator is not None:
            await asyncio.to_thread(self._record_clients, now, entries, known)
        return True

    def _since(self, now: int) -> datetime:
        """Read the query log from the last entry seen; at most CATCH_UP_MS back."""
        if self._cursor is None:
            self._cursor = parse_time(self.store.kv_get(CURSOR_KEY, None))
        if self._cursor is None:  # first poll ever: just the last interval
            return datetime.fromtimestamp((now - POLL_S * 1000) / 1000, UTC)
        return max(self._cursor, datetime.fromtimestamp((now - CATCH_UP_MS) / 1000, UTC))

    def _unreachable_poll(self, now: int) -> None:
        self._unreachable += 1
        self.adguard_store.add({"ts_ms": now, "reachable": False, "source": "stats"})
        if self._unreachable >= UNREACHABLE_AFTER and "unreachable" not in self._open:
            self._open["unreachable"] = self.events.open(
                DNS,
                "critical",
                now - (self._unreachable - 1) * POLL_S * 1000,
                "AdGuard Home is unreachable: devices using it cannot resolve names",
                details={"type": "adguard_unreachable", "error": self.error},
            )

    def _close(self, key: str, now: int, extra: dict | None = None) -> None:
        eid = self._open.pop(key, None)
        if eid is not None:
            self.events.close(eid, now, extra)

    def _close_all(self, now: int) -> None:
        for key in list(self._open):
            self._close(key, now, {"closed_because": "integration turned off"})
        self._clean.clear()

    def _apply(self, now, status, entries, cut, stats, known) -> None:
        self._unreachable = 0
        self._close("unreachable", now)
        if entries:
            self._cursor = max(e["_ts"] for e in entries)
            self.store.kv_set(CURSOR_KEY, self._cursor.isoformat())
        if stats is None:
            m = interval_metrics(entries)
            source = "querylog"
        else:
            m = self._from_stats(stats)
            source = "stats"
        row = {
            "ts_ms": now,
            "reachable": True,
            "protection": status["protection"],
            "queries": m["queries"],
            "blocked": m["blocked"],
            "servfail": m["servfail"],
            "errors": m["errors"],
            "avg_ms": m["avg_ms"],
            "p95_ms": m["p95_ms"],
            "source": source,
            "upstreams": m["upstreams"],
            "details": {
                "version": status.get("version"),
                "top_clients": m["top_clients"],
                "sampled": cut,
            },
        }
        self.adguard_store.add(row)
        self._protection(now, status)
        self._upstreams(now, m["upstreams"])
        self._errors(now, m)
        self._latency(now, m["avg_ms"])
        self._clients(now, [c for c, _ in m["top_clients"]], entries, known)

    # ── per-client history and the device inventory ─────────────────────────

    def _record_clients(self, now: int, entries: list[dict], known: list[dict]) -> None:
        touched = self.accumulator.add(entries)
        device_of: dict[str, int | None] = {}
        if touched:
            clients = {c for h in touched for c in self.accumulator.hours.get(h, {})}
            device_of = self._devices_for(clients, known)
            self.client_store.put(self.accumulator.rows(touched, device_of))
        self.accumulator.prune(now)
        per_device: dict[int, tuple[int, int]] = {}
        for e in entries:
            dev = device_of.get(str(e.get("client") or "")[:64])
            if dev is not None:
                q, b = per_device.get(dev, (0, 0))
                per_device[dev] = (q + 1, b + (entry_outcome(e) == "blocked"))
        self._check_rates(now, per_device)

    # ── per-device rate alerts ───────────────────────────────────────────────

    def _baselines(self, now: int) -> dict[int, tuple[float, float, int]]:
        """Median hourly queries, usual blocked share and hours of history, per device."""
        h = hour_of(now)
        if h != self._baseline_hour:
            hours = self.client_store.device_hours(h - 7 * 24 * HOUR_MS, h - HOUR_MS)
            self._baseline = {
                dev: (
                    statistics.median(q for q, _ in rows),
                    sum(b for _, b in rows) / max(1, sum(q for q, _ in rows)),
                    len(rows),
                )
                for dev, rows in hours.items()
            }
            self._baseline_hour = h
        return self._baseline

    def _check_rates(self, now: int, per_device: dict[int, tuple[int, int]]) -> None:
        self._recent.append((now, per_device))
        while self._recent and self._recent[0][0] <= now - HOUR_MS:
            self._recent.popleft()
        totals: dict[int, list[int]] = {}
        for _, counts in self._recent:
            for dev, (q, b) in counts.items():
                t = totals.setdefault(dev, [0, 0])
                t[0] += q
                t[1] += b
        base = self._baselines(now)
        for dev, (q, b) in totals.items():
            median, usual, hours = base.get(dev, (0.0, 0.0, 0))
            if hours < RATE_HISTORY_HOURS:
                continue
            if q > max(RATE_MIN_QUERIES, RATE_FACTOR * median):
                self._rate_alert(now, "dns_query_rate", dev, q, b, median, usual,
                                 "{label} made {q:,} DNS queries in the last hour "
                                 "(usually about {usual_q:,})")  # fmt: skip
            if q >= BLOCKED_MIN_QUERIES and b / q >= BLOCKED_SHARE and usual < BLOCKED_USUAL_MAX:
                self._rate_alert(now, "dns_blocked_rate", dev, q, b, median, usual,
                                 "{share}% of {label}'s DNS queries were blocked in the last hour "
                                 "(usually {usual_share}%)")  # fmt: skip

    def _rate_alert(self, now, typ, dev, q, b, median, usual, title) -> None:
        key = f"{typ}:{dev}"
        if now - self._rate_last.get(key, -(10**15)) < RATE_COOLDOWN_MS:
            return
        recent = self._rate_times.setdefault(typ, deque())
        while recent and recent[0] <= now - HOUR_MS:
            recent.popleft()
        if len(recent) >= RATE_MAX_PER_HOUR:
            return
        row = self.inventory.store.get(dev) if self.inventory is not None else None
        if row is None:
            return
        if row.get("merged_into"):
            row = self.inventory.store.get(row["merged_into"]) or row
        recent.append(now)
        self._rate_last[key] = now
        label = device_label(row)
        text = title.format(
            label=label, q=q, usual_q=round(median), share=round(100 * b / q),
            usual_share=round(100 * usual))  # fmt: skip
        self.events.instant(
            ANOMALY,
            "warning",
            now,
            text,
            details={
                "type": typ, "device_id": row["id"], "mac": row["mac"], "device": label,
                "queries": q, "blocked": b, "usual_queries": round(median, 1),
                "usual_blocked_pct": round(100 * usual, 1), "hint": HINTS.get(typ, ""),
            },
        )  # fmt: skip
        log.warning("DNS rate anomaly", extra={"anomaly": typ, "device": label})

    def _devices_for(self, clients: set[str], known: list[dict]) -> dict[str, int | None]:
        """Device id per client: by MAC when AdGuard groups the id with one, else by IP."""
        macs = {k["id"]: k["group"] for k in known if k.get("kind") == "mac"}
        group_mac = {g: mac for mac, g in macs.items()}
        out: dict[str, int | None] = {c: None for c in clients}
        by_mac: dict[str, str] = {}
        for k in known:
            if k["id"] in clients and k.get("group") is not None and k["group"] in group_mac:
                by_mac[k["id"]] = group_mac[k["group"]]
        with self.store.engine.connect() as conn:
            if by_mac:
                rows = conn.execute(
                    select(devices.c.id, devices.c.mac).where(
                        devices.c.mac.in_(set(by_mac.values()))
                    )
                ).all()
                ids = {mac: i for i, mac in rows}
                for c, mac in by_mac.items():
                    out[c] = ids.get(mac)
            ips = [c for c in clients if out[c] is None]
            if ips:
                stmt = (
                    select(device_ips.c.ip, device_ips.c.device_id)
                    .where(device_ips.c.ip.in_(ips))
                    .order_by(device_ips.c.last_seen_ms)  # the latest holder wins
                )
                for ip, dev in conn.execute(stmt):
                    out[ip] = dev
        return out

    def _feed_inventory(self, now: int, known: list[dict], dhcp: dict) -> None:
        """Names given to clients in AdGuard and its DHCP leases, as discovery observations."""
        names: dict[str, str] = {}  # MAC → name of the persistent client it belongs to
        group_name = {k["group"]: k["name"] for k in known if k.get("group") is not None}
        for k in known:
            if k.get("kind") == "mac" and group_name.get(k["group"]):
                names[k["id"]] = group_name[k["group"]]
        obs: dict[str, Observation] = {}
        for mac, name in names.items():
            obs[mac] = Observation(now, "adguard", mac, info={"adguard_name": name})
        if dhcp.get("enabled"):
            for lease in dhcp.get("leases") or []:
                info = {"hostname": lease["hostname"], "lease": True}
                if lease["mac"] in names:
                    info["adguard_name"] = names[lease["mac"]]
                obs[lease["mac"]] = Observation(now, "adguard", lease["mac"], ip=lease["ip"],
                                                info=info)  # fmt: skip
        # A persistent client known only by IP names the device currently at that address.
        with_mac = {k["group"] for k in known if k.get("kind") == "mac"}
        for k in known:
            if k.get("kind") != "ip" or k.get("group") is None or not group_name.get(k["group"]):
                continue
            if k["group"] in with_mac:
                continue  # named by its MAC above
            holders = [d for d in self.inventory.store.with_ip(k["id"]) if d["merged_into"] is None]
            if len(holders) == 1 and holders[0]["mac"] not in obs:
                mac = holders[0]["mac"]
                obs[mac] = Observation(now, "adguard", mac,
                                       info={"adguard_name": group_name[k["group"]]})  # fmt: skip
        if obs:
            self.inventory.apply(list(obs.values()))

    def _from_stats(self, s: dict) -> dict:
        """Fallback when the query log is off: deltas of AdGuard's rolling totals."""
        prev, self._prev_stats = self._prev_stats, s

        def delta(key: str) -> int | None:
            if prev is None:
                return None
            d = int(s.get(key) or 0) - int(prev.get(key) or 0)
            return d if d >= 0 else None  # the rolling window moved past old data

        times = _pairs(s.get("top_upstreams_avg_time"))
        counts = _pairs(s.get("top_upstreams_responses"))
        ups = {
            str(k)[:120]: {
                "n": int(counts.get(k) or 0),
                "fail": None,
                "avg_ms": round(float(v) * 1000, 1),
            }
            for k, v in times.items()
        }
        avg = s.get("avg_processing_time")
        clients = sorted(_pairs(s.get("top_clients")).items(), key=lambda kv: -int(kv[1]))
        return {
            "queries": delta("num_dns_queries"),
            "blocked": delta("num_blocked_filtering"),
            "servfail": None,
            "errors": None,
            "avg_ms": round(float(avg) * 1000, 1) if avg is not None else None,
            "p95_ms": None,
            "upstreams": ups,
            "top_clients": [(str(c)[:64], int(n)) for c, n in clients[:5]],
        }

    # ── events ───────────────────────────────────────────────────────────────

    def _protection(self, now: int, status: dict) -> None:
        if not status["protection"] and "protection" not in self._open:
            self._open["protection"] = self.events.open(
                DNS,
                "warning",
                now,
                "AdGuard Home protection is off: nothing is being blocked",
                details={"type": "protection_disabled"},
            )
        elif status["protection"]:
            self._close("protection", now)

    def _upstreams(self, now: int, ups: dict) -> None:
        for name, u in ups.items():
            key = f"up:{name}"
            fail, n = u.get("fail"), u.get("n") or 0
            failing = (
                fail is not None and n >= UPSTREAM_MIN_QUERIES and fail / n >= UPSTREAM_FAIL_RATE
            )
            if failing:
                self._clean.pop(key, None)
                if key not in self._open:
                    self._open[key] = self.events.open(
                        DNS,
                        "warning",
                        now,
                        f"DNS upstream {name} is failing ({fail} of {n} queries)",
                        details={"type": "upstream_failing", "upstream": name,
                                 "fail": fail, "queries": n},
                    )  # fmt: skip
        for key in [k for k in self._open if k.startswith("up:")]:
            u = ups.get(key[3:]) or {}
            n, fail = u.get("n") or 0, u.get("fail") or 0
            if n < UPSTREAM_MIN_QUERIES or fail / n < UPSTREAM_FAIL_RATE:
                self._clean[key] = self._clean.get(key, 0) + 1
                if self._clean[key] >= UPSTREAM_CLEAN_TO_CLOSE:
                    self._clean.pop(key)
                    self._close(key, now)

    def _errors(self, now: int, m: dict) -> None:
        q, bad = m["queries"] or 0, (m["servfail"] or 0) + (m["errors"] or 0)
        answered = q - (m["blocked"] or 0)
        if (
            answered >= ERROR_MIN_QUERIES
            and bad / answered >= ERROR_RATE
            and now - self._last_error_ms > ERROR_COOLDOWN_MS
        ):
            self._last_error_ms = now
            self.events.instant(
                DNS,
                "warning",
                now,
                f"DNS errors spiked: {bad} of {answered} queries failed "
                f"({100 * bad / answered:.0f} %, {m['servfail'] or 0} SERVFAIL)",
                details={"type": "dns_errors", "failed": bad, "queries": answered,
                         "servfail": m["servfail"]},
            )  # fmt: skip

    def _latency(self, now: int, avg_ms: float | None) -> None:
        if self._avg_history is None:
            rows = self.adguard_store.history(now - 86_400_000, now, limit=1440)
            self._avg_history = deque(
                (r["avg_ms"] for r in rows if r["avg_ms"] is not None), maxlen=1440
            )
        if avg_ms is None:
            return
        hist = self._avg_history
        if len(hist) >= SPIKE_MIN_HISTORY:
            median = statistics.median(hist)
            limit = max(SPIKE_FLOOR_MS, SPIKE_FACTOR * median)
            if avg_ms > limit and now - self._last_spike_ms > SPIKE_COOLDOWN_MS:
                self._last_spike_ms = now
                self.events.instant(
                    DNS,
                    "warning",
                    now,
                    f"DNS latency spike: {avg_ms:.0f} ms on average (usually {median:.0f} ms)",
                    details={"type": "latency_spike", "avg_ms": avg_ms,
                             "median_ms": round(median, 1)},
                )  # fmt: skip
        hist.append(avg_ms)

    def _known_ip(self, ip: str) -> bool:
        with self.store.engine.connect() as conn:
            return (
                conn.execute(select(device_ips.c.ip).where(device_ips.c.ip == ip)).first()
                is not None
            )

    def _clients(self, now: int, top: list[str], entries: list[dict], known: list[dict]) -> None:
        seen = {str(e.get("client"))[:64] for e in entries if e.get("client")} | set(top)
        names = {k["id"]: k["name"] for k in known}
        seen |= set(names)
        stored = self.store.kv_get(CLIENTS_KEY, None)
        if stored is None:  # first poll: learn the current clients without alerting
            self.store.kv_set(CLIENTS_KEY, sorted(seen)[:MAX_KNOWN_CLIENTS])
            return
        known_set = set(stored)
        new = sorted(seen - known_set)
        if not new:
            return
        while self._new_client_times and self._new_client_times[0] < now - 3600_000:
            self._new_client_times.popleft()
        for cid in new:
            if self._known_ip(cid) or len(self._new_client_times) >= NEW_CLIENTS_PER_HOUR:
                continue
            self._new_client_times.append(now)
            label = names.get(cid) or cid
            self.events.instant(
                DNS,
                "info",
                now,
                f"New DNS client: {label}",
                details={"type": "new_client", "client": cid, "name": names.get(cid)},
            )
        self.store.kv_set(CLIENTS_KEY, sorted(known_set | set(new))[-MAX_KNOWN_CLIENTS:])

    # ── admin ────────────────────────────────────────────────────────────────

    async def test(self, cfg: dict, password: str) -> dict:
        client = self.client_factory(cfg, password)
        try:
            status = await client.status()
            stats = await client.stats()
            querylog = await client.querylog_enabled()
        except AdGuardError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            await client.aclose()
        return {
            "ok": True,
            "version": status.get("version"),
            "protection": status["protection"],
            "queries_24h": stats.get("num_dns_queries"),
            "querylog": querylog,
        }

    def status(self) -> dict:
        return {
            **self.config(),
            "password_set": self.store.kv_get(PASSWORD_KEY, None) is not None,
            "last_poll_ms": self.last_poll_ms,
            "last_ok_ms": self.last_ok_ms,
            "error": self.error,
            "paused": self.paused,
            "status": self.last,
            "latest": self.adguard_store.latest(),
            "dhcp": None
            if self.dhcp is None
            else {
                "available": self.dhcp["available"],
                "enabled": self.dhcp["enabled"],
                "leases": len(self.dhcp["leases"]),
            },
        }

    # ── DNS page ────────────────────────────────────────────────────────────

    def summary(self, hours: int) -> dict:
        """Series, block rate, per-upstream health and top clients for the DNS page."""
        end = int(time.time() * 1000)
        rows = self.adguard_store.history(end - hours * 3_600_000, end + 1)
        series = [
            {k: r[k] for k in ("ts_ms", "reachable", "queries", "blocked", "avg_ms", "p95_ms")}
            for r in rows
        ]
        ok = [r for r in rows if r["reachable"]]
        queries = sum(r["queries"] or 0 for r in ok)
        blocked = sum(r["blocked"] or 0 for r in ok)
        failed = sum((r["servfail"] or 0) + (r["errors"] or 0) for r in ok)
        ups: dict[str, dict] = {}
        for r in ok:
            for name, u in (r["upstreams"] or {}).items():
                blank = {"name": name, "queries": 0, "failed": 0, "_ms": 0.0, "_n": 0}
                a = ups.setdefault(name, blank)
                a["queries"] += int(u.get("n") or 0)
                a["failed"] += int(u.get("fail") or 0)
                if u.get("avg_ms") is not None:
                    a["_ms"] += float(u["avg_ms"]) * int(u.get("n") or 0)
                    a["_n"] += int(u.get("n") or 0)
        upstreams = []
        for a in ups.values():
            ms, n = a.pop("_ms"), a.pop("_n")
            a["avg_ms"] = round(ms / n, 1) if n else None
            a["failed_pct"] = round(100 * a["failed"] / a["queries"], 1) if a["queries"] else 0.0
            upstreams.append(a)
        upstreams.sort(key=lambda a: -a["queries"])
        counts: Counter[str] = Counter()
        for r in ok:
            for cid, n in (r["details"] or {}).get("top_clients") or []:
                counts[str(cid)] += int(n)
        top = counts.most_common(10)
        labels = self._labels([c for c, _ in top])
        clients = [
            {"client": c, "queries": n, **labels.get(c, {"device_id": None, "label": None})}
            for c, n in top
        ]
        return {
            **self.status(),
            "hours": hours,
            "series": series,
            "queries": queries,
            "blocked_pct": round(100 * blocked / queries, 1) if queries else None,
            "failed_pct": round(100 * failed / max(1, queries - blocked), 1) if queries else None,
            "upstreams": upstreams,
            "top_clients": clients,
            **(
                self.client_store.network(end - hours * 3_600_000, end + 1)
                if self.client_store is not None
                else {"top_domains": [], "top_blocked": [], "unknown_clients": []}
            ),
        }

    def _labels(self, ips: list[str]) -> dict[str, dict]:
        if not ips:
            return {}
        stmt = (
            select(device_ips.c.ip, devices)
            .join(devices, devices.c.id == device_ips.c.device_id)
            .where(device_ips.c.ip.in_(ips))
            .order_by(device_ips.c.last_seen_ms)
        )
        out: dict[str, dict] = {}
        with self.store.engine.connect() as conn:
            for r in conn.execute(stmt):
                d = dict(r._mapping)
                try:
                    d["details"] = json.loads(d.get("details") or "{}")
                except ValueError:
                    d["details"] = {}
                out[d["ip"]] = {"device_id": d["id"], "label": device_label(d)}
        return out
