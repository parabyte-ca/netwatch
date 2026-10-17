"""Device inventory: turns observations into device rows, IP history and type guesses."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy import Engine, and_, delete, func, insert, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db import device_ips, devices
from .classify import classify, is_randomised, mergeable_name, vendor_for
from .packets import Observation, usable_ip

ONLINE_MS = 15 * 60_000  # seen within this long = online
WRITE_EVERY_MS = 60_000  # last-seen timestamps are written at most this often per device
_MAX_LIST = 12


def _row(r) -> dict:
    d = dict(r._mapping)
    d["details"] = json.loads(d.get("details") or "{}")
    return d


_ROLE_LABEL = {"default gateway": "Gateway", "this NetWatch host": "NetWatch host"}


def device_label(d: dict) -> str:
    det = d.get("details") or {}
    return (
        d.get("name")
        or det.get("friendly")
        or d.get("mdns_name")
        or d.get("hostname")
        or (det["ptr"].split(".")[0] if det.get("ptr") else None)
        or d.get("model")
        or _ROLE_LABEL.get(d.get("type_reason") or "")
        or (f"{d['vendor']} device" if d.get("vendor") else None)
        or ("Private-address device" if d.get("randomised") else d["mac"])
    )


class DeviceStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, device_id: int) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(devices).where(devices.c.id == device_id)).first()
        return _row(r) if r else None

    def get_by_mac(self, mac: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(devices).where(devices.c.mac == mac)).first()
        return _row(r) if r else None

    def insert(self, values: dict) -> int:
        values = {**values, "details": json.dumps(values.get("details") or {})}
        with self.engine.begin() as conn:
            return conn.execute(insert(devices).values(**values)).inserted_primary_key[0]

    def update(self, device_id: int, values: dict) -> None:
        if "details" in values:
            values = {**values, "details": json.dumps(values["details"])}
        with self.engine.begin() as conn:
            conn.execute(update(devices).where(devices.c.id == device_id).values(**values))

    def delete(self, device_id: int) -> bool:
        with self.engine.begin() as conn:
            conn.execute(
                update(devices).where(devices.c.merged_into == device_id).values(merged_into=None)
            )
            return conn.execute(delete(devices).where(devices.c.id == device_id)).rowcount > 0

    def upsert_ip(self, device_id: int, ip: str, ts_ms: int, source: str) -> None:
        stmt = sqlite_insert(device_ips).values(
            device_id=device_id, ip=ip, first_seen_ms=ts_ms, last_seen_ms=ts_ms, source=source
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[device_ips.c.device_id, device_ips.c.ip],
            set_={"last_seen_ms": func.max(device_ips.c.last_seen_ms, stmt.excluded.last_seen_ms)},
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def ips(self, device_ids: Iterable[int]) -> list[dict]:
        ids = list(device_ids)
        stmt = (
            select(device_ips)
            .where(device_ips.c.device_id.in_(ids))
            .order_by(device_ips.c.last_seen_ms.desc())
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def all(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(select(devices).order_by(devices.c.id))]

    def members(self, primary_id: int) -> list[dict]:
        stmt = select(devices).where(devices.c.merged_into == primary_id).order_by(devices.c.id)
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(stmt)]

    def merge_candidate(self, hostname: str, exclude_id: int) -> int | None:
        """The primary device of another randomised MAC that reported the same hostname."""
        stmt = (
            select(devices.c.id, devices.c.merged_into)
            .where(
                devices.c.randomised.is_(True),
                func.lower(devices.c.hostname) == hostname,
                devices.c.id != exclude_id,
            )
            .order_by(devices.c.first_seen_ms)
            .limit(1)
        )
        with self.engine.connect() as conn:
            r = conn.execute(stmt).first()
        if r is None:
            return None
        return r.merged_into or r.id

    def with_ip(self, ip: str) -> list[dict]:
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(select(devices).where(devices.c.last_ip == ip))]

    def count(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(devices)).scalar_one()

    def purge(self, cutoff_ms: int) -> int:
        """Forget devices unseen since cutoff, unless named or monitored."""
        cond = and_(
            devices.c.last_seen_ms < cutoff_ms,
            devices.c.name.is_(None),
            devices.c.target_id.is_(None),
        )
        with self.engine.begin() as conn:
            ids = [r[0] for r in conn.execute(select(devices.c.id).where(cond))]
            if not ids:
                return 0
            conn.execute(
                update(devices).where(devices.c.merged_into.in_(ids)).values(merged_into=None)
            )
            conn.execute(delete(device_ips).where(device_ips.c.device_id.in_(ids)))
            return conn.execute(delete(devices).where(devices.c.id.in_(ids))).rowcount

    def purge_ips(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(device_ips).where(device_ips.c.last_seen_ms < cutoff_ms)
            ).rowcount

    def by_target(self, target_id: int) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(devices).where(devices.c.target_id == target_id)).first()
        return _row(r) if r else None

    def search_ids(self, q: str) -> set[int]:
        like = f"%{q.lower()}%"
        cols = (
            devices.c.mac,
            devices.c.name,
            devices.c.hostname,
            devices.c.mdns_name,
            devices.c.vendor,
            devices.c.model,
            devices.c.last_ip,
        )
        stmt = select(devices.c.id, devices.c.merged_into).where(
            or_(*[func.lower(c).like(like) for c in cols])
        )
        with self.engine.connect() as conn:
            return {r.merged_into or r.id for r in conn.execute(stmt)}


@dataclass
class Change:
    kind: str  # new | ip
    device: dict
    old_ip: str | None = None


@dataclass
class _Cached:
    id: int
    last_ip: str | None
    last_write_ms: int
    row: dict
    ip_writes: dict[str, int] = field(default_factory=dict)


class Inventory:
    """Applies observations to the device table. Thread-safe; writes are throttled so a chatty
    ARP neighbour costs one row update per minute, not one per packet."""

    def __init__(
        self,
        store: DeviceStore,
        *,
        own_macs: Callable[[], set[str]] = set,
        gateway_ip: Callable[[], str | None] = lambda: None,
    ) -> None:
        self.store = store
        self.own_macs = own_macs
        self.gateway_ip = gateway_ip
        self._cache: dict[str, _Cached] = {}
        self._lock = threading.Lock()

    def forget(self, mac: str | None = None) -> None:
        """Drop cached state (after an API edit) so the next observation reloads the row."""
        with self._lock:
            if mac is None:
                self._cache.clear()
            else:
                self._cache.pop(mac, None)

    def apply(self, observations: Iterable[Observation]) -> list[Change]:
        changes: list[Change] = []
        with self._lock:
            for obs in observations:
                changes.extend(self._apply(obs))
        return changes

    def _load(self, mac: str, ts_ms: int) -> tuple[_Cached, bool]:
        c = self._cache.get(mac)
        if c is not None:
            return c, False
        row = self.store.get_by_mac(mac)
        created = False
        if row is None:
            vendor = vendor_for(mac)
            values = {
                "mac": mac,
                "vendor": vendor,
                "randomised": is_randomised(mac),
                "type": "unknown",
                "type_reason": "no identifying traffic yet",
                "first_seen_ms": ts_ms,
                "last_seen_ms": ts_ms,
                "details": {},
            }
            values["id"] = self.store.insert(values)
            row = {**values, "name": None, "hostname": None, "mdns_name": None, "model": None,
                   "type_override": None, "merged_into": None, "target_id": None,
                   "last_ip": None, "notes": None}  # fmt: skip
            created = True
        c = _Cached(row["id"], row.get("last_ip"), row["last_seen_ms"], row)
        self._cache[mac] = c
        return c, created

    def _apply(self, obs: Observation) -> list[Change]:
        c, created = self._load(obs.mac, obs.ts_ms)
        row = c.row
        det = dict(row.get("details") or {})
        upd: dict = {}
        info = obs.info

        def add_to(key: str, values: Iterable[str]) -> None:
            cur = list(det.get(key) or [])
            for v in values:
                if v and v not in cur and len(cur) < _MAX_LIST:
                    cur.append(v)
            if cur != (det.get(key) or []):
                det[key] = cur

        add_to("sources", [obs.proto])
        if obs.proto == "dhcp" and info.get("op") == 1:
            if info.get("hostname") and info["hostname"] != row.get("hostname"):
                upd["hostname"] = info["hostname"][:64]
            for key, src in (("dhcp_vendor", "vendor_class"), ("dhcp_prl", "prl")):
                if info.get(src) and det.get(key) != info[src]:
                    det[key] = info[src]
        elif obs.proto == "dhcp" and info.get("op") == 2:
            det["dhcp_server"] = True
        elif obs.proto == "mdns":
            names = info.get("names") or []
            if names and row.get("mdns_name") not in names:
                upd["mdns_name"] = names[0][:64]
            add_to("services", info.get("services") or [])
            if info.get("model") and info["model"] != row.get("model"):
                upd["model"] = info["model"][:64]
            if info.get("friendly") and det.get("friendly") != info["friendly"]:
                det["friendly"] = info["friendly"]
        elif obs.proto == "ssdp":
            add_to("ssdp", [info.get("server"), info.get("type")])
        elif obs.proto == "rdns" and info.get("ptr") and det.get("ptr") != info["ptr"]:
            det["ptr"] = info["ptr"]
        if det != (row.get("details") or {}):
            upd["details"] = det

        changes: list[Change] = []
        if created:
            changes.append(Change("new", row))
        ip = obs.ip if usable_ip(obs.ip) else None
        if ip and ip != c.last_ip:
            old = c.last_ip
            upd["last_ip"] = ip
            self.store.upsert_ip(c.id, ip, obs.ts_ms, obs.proto)
            c.ip_writes[ip] = obs.ts_ms
            c.last_ip = ip
            if old is not None:
                changes.append(Change("ip", row, old_ip=old))
        elif ip and obs.ts_ms - c.ip_writes.get(ip, 0) >= WRITE_EVERY_MS:
            self.store.upsert_ip(c.id, ip, obs.ts_ms, obs.proto)
            c.ip_writes[ip] = obs.ts_ms

        identity = {"hostname", "mdns_name", "model", "details", "last_ip"}
        if created or identity & upd.keys():
            merged = {**row, **upd}
            t, reason = self._classify(merged)
            if (t, reason) != (row.get("type"), row.get("type_reason")):
                upd["type"], upd["type_reason"] = t, reason[:120]
        if (
            "hostname" in upd
            and row.get("randomised")
            and row.get("merged_into") is None
            and (norm := mergeable_name(upd["hostname"]))
        ):
            primary = self.store.merge_candidate(norm, c.id)
            if primary is not None and primary != c.id:
                upd["merged_into"] = primary

        if upd or obs.ts_ms - c.last_write_ms >= WRITE_EVERY_MS:
            upd["last_seen_ms"] = max(obs.ts_ms, row.get("last_seen_ms") or 0)
            self.store.update(c.id, upd)
            c.last_write_ms = obs.ts_ms
            row.update(upd)
            if "details" in upd:
                row["details"] = det
        return changes

    def _classify(self, d: dict) -> tuple[str, str]:
        det = d.get("details") or {}
        names = [n for n in (det.get("friendly"), d.get("mdns_name"), d.get("hostname"),
                             det.get("ptr"), d.get("model")) if n]  # fmt: skip
        gw = self.gateway_ip()
        return classify(
            vendor=d.get("vendor"),
            names=names,
            services=det.get("services") or [],
            dhcp_vendor=det.get("dhcp_vendor"),
            ssdp=det.get("ssdp") or [],
            is_gateway=bool(gw) and d.get("last_ip") == gw,
            is_self=d["mac"] in self.own_macs(),
            is_dhcp_server=bool(det.get("dhcp_server")),
        )

    # ── read model for the API ────────────────────────────────────────────────

    def listing(self, now_ms: int, *, q: str | None = None) -> list[dict]:
        rows = self.store.all()
        by_id = {r["id"]: r for r in rows}
        groups: dict[int, list[dict]] = {}
        for r in rows:
            primary = r["merged_into"] if r["merged_into"] in by_id else r["id"]
            groups.setdefault(primary, []).append(r)
        wanted = self.store.search_ids(q) if q else None
        out = []
        for pid, members in groups.items():
            if wanted is not None and pid not in wanted:
                continue
            out.append(summarise_group(by_id[pid], members, now_ms))
        out.sort(key=lambda d: (not d["online"], d["label"].lower()))
        return out


def summarise_group(primary: dict, members: list[dict], now_ms: int) -> dict:
    freshest = max(members, key=lambda m: m["last_seen_ms"])
    last_seen = freshest["last_seen_ms"]
    d = {
        **primary,
        "type": primary["type_override"] or primary["type"],
        "type_auto": primary["type"],
        "label": device_label(primary),
        "last_seen_ms": last_seen,
        "first_seen_ms": min(m["first_seen_ms"] for m in members),
        "last_ip": freshest["last_ip"] or primary["last_ip"],
        "online": now_ms - last_seen <= ONLINE_MS,
        "macs": [m["mac"] for m in sorted(members, key=lambda m: m["id"])],
    }
    return d
