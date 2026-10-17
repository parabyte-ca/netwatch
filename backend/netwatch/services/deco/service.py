"""Deco mesh integration: polls the Decos for their units and connected clients, keeps the
device inventory informed (which Deco, band, wired or Wi-Fi, app names) and records Wi-Fi
events: Deco offline/online, backhaul changes, and roaming / disconnects of monitored devices.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from ..discovery.inventory import DeviceStore, Inventory, device_label
from ..discovery.packets import Observation
from ..events import WIFI, EventLog
from ..secrets import SecretBox
from ..storage import Store
from .client import DecoClient, DecoError

log = logging.getLogger(__name__)

CONFIG_KEY = "deco_config"
PASSWORD_KEY = "deco_password"  # noqa: S105 - the kv key, not a password
POLL_S = 30
DISCONNECT_AFTER_MS = 60_000

BAND_LABEL = {
    "band2_4": "2.4 GHz",
    "band5": "5 GHz",
    "band5_2": "5 GHz-2",
    "band6": "6 GHz",
    "wired": "wired",
}


def norm_mac(mac: str | None) -> str:
    return (mac or "").strip().lower().replace("-", ":")


def _backhaul(conn) -> str:
    """Deco connection_type is a string or a list of bands; reduce it to one label."""
    items = conn if isinstance(conn, list) else [conn] if conn else []
    items = [str(i).lower() for i in items]
    if not items:
        return "unknown"
    if any("wire" in i or i == "ethernet" for i in items):
        return "wired"
    return " + ".join(BAND_LABEL.get(i, i) for i in items)


def _title(nickname: str | None) -> str | None:
    if not nickname:
        return None
    return nickname.replace("_", " ").strip().title()


def normalize_deco(d: dict) -> dict:
    mac = norm_mac(d.get("mac"))
    name = d.get("custom_nickname") or _title(d.get("nickname")) or mac[-5:]
    return {
        "mac": mac,
        "raw_mac": d.get("mac"),  # the Deco's own format, used when querying its clients
        "name": name,
        "ip": d.get("device_ip"),
        "model": d.get("device_model"),
        "hardware": d.get("hardware_ver"),
        "firmware": d.get("software_ver"),
        "role": "main" if d.get("role") == "master" else "satellite",
        "online": d.get("group_status") in (None, "connected"),
        "internet": d.get("inet_status"),
        "backhaul": _backhaul(d.get("connection_type")),
        "signal": d.get("signal_level") or {},
        "backhaul_speed": d.get("backhual_speed"),
    }


def normalize_client(c: dict, deco_mac: str) -> dict:
    conn = str(c.get("connection_type") or "").lower()
    return {
        "mac": norm_mac(c.get("mac")),
        "name": c.get("name") or None,
        "ip": c.get("ip") if c.get("ip") not in ("", "0.0.0.0", "UNKNOWN") else None,
        "online": bool(c.get("online", True)),
        "connection": BAND_LABEL.get(conn, conn or "unknown"),
        "wired": conn == "wired",
        "guest": c.get("interface") == "guest",
        "deco_mac": deco_mac,
        "down_bps": (c.get("down_speed") or 0) * 8,
        "up_bps": (c.get("up_speed") or 0) * 8,
    }


ClientFactory = Callable[[str, str], DecoClient]


class DecoService:
    def __init__(
        self,
        store: Store,
        events: EventLog,
        devices: DeviceStore,
        inventory: Inventory,
        secrets: SecretBox,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.store = store
        self.events = events
        self.devices = devices
        self.inventory = inventory
        self.secrets = secrets
        self.client_factory = client_factory or (lambda host, pw: DecoClient(host, pw))
        self.decos: dict[str, dict] = {}
        self.clients: dict[str, dict] = {}
        self.last_poll_ms: int | None = None
        self.last_ok_ms: int | None = None
        self.error: str | None = None
        self._client: DecoClient | None = None
        self._client_key: tuple[str, str] | None = None
        self._task: asyncio.Task | None = None
        self._kick: asyncio.Task | None = None
        self._deco_events: dict[str, int] = {}  # deco mac → open offline event
        self._gone_since: dict[str, int] = {}  # monitored device mac → first missed poll
        self._wifi_events: dict[str, int] = {}  # monitored device mac → open disconnect event
        self._last_pos: dict[str, str] = {}  # monitored device mac → where it was last seen

    # ── configuration ────────────────────────────────────────────────────────

    def config(self) -> dict:
        c = self.store.kv_get(CONFIG_KEY, {}) or {}
        return {"host": c.get("host", ""), "enabled": bool(c.get("enabled", False))}

    def password(self) -> str | None:
        token = self.store.kv_get(PASSWORD_KEY, None)
        return self.secrets.decrypt(token) if token else None

    def save(self, host: str, enabled: bool, password: str | None) -> None:
        self.store.kv_set(CONFIG_KEY, {"host": host.strip(), "enabled": enabled})
        if password:
            self.store.kv_set(PASSWORD_KEY, self.secrets.encrypt(password))

    def forget_password(self) -> None:
        self.store.kv_set(PASSWORD_KEY, None)

    @property
    def configured(self) -> bool:
        c = self.config()
        return bool(c["host"]) and self.store.kv_get(PASSWORD_KEY, None) is not None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-deco")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._drop_client()

    def kick(self) -> None:
        """Poll now (after the settings change) instead of waiting for the next cycle."""
        if self.config()["enabled"] and self.configured:
            self._kick = asyncio.create_task(self.poll(), name="netwatch-deco-kick")

    async def _drop_client(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None
        self._client_key = None

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                if self.config()["enabled"] and self.configured:
                    await self.poll()
            except Exception:
                log.exception("Deco poll failed")
            await asyncio.sleep(POLL_S)

    # ── polling ──────────────────────────────────────────────────────────────

    async def _fetch(self, client: DecoClient) -> tuple[list[dict], list[dict]]:
        decos = [normalize_deco(d) for d in await client.list_decos()]
        clients: list[dict] = []
        per_deco_failed = False
        for d in decos:
            if not d["online"]:
                continue
            try:
                for c in await client.list_clients(d["raw_mac"] or "default"):
                    clients.append(normalize_client(c, d["mac"]))
            except DecoError:
                per_deco_failed = True
        if per_deco_failed or (decos and not clients):
            # Some firmwares only answer the global query; we then lose which Deco a
            # client is on (the field may be absent) but keep the rest.
            clients = []
            for c in await client.list_clients("default"):
                clients.append(normalize_client(c, norm_mac(c.get("access_host") or "")))
        return decos, clients

    async def poll(self, now_ms: int | None = None) -> bool:
        cfg = self.config()
        pw = self.password()
        now = now_ms or int(time.time() * 1000)
        self.last_poll_ms = now
        if not pw:
            self.error = "the stored password cannot be decrypted; enter it again"
            return False
        key = (cfg["host"], pw)
        if self._client_key != key:
            await self._drop_client()
            self._client = self.client_factory(cfg["host"], pw)
            self._client_key = key
        assert self._client is not None  # noqa: S101 - set just above
        try:
            decos, clients = await self._fetch(self._client)
        except DecoError as exc:
            self.error = str(exc)
            log.warning("Deco poll failed", extra={"error": self.error})
            return False
        self.error = None
        self.last_ok_ms = now
        await asyncio.to_thread(self._apply, decos, clients, now)
        return True

    def _apply(self, decos: list[dict], clients: list[dict], now: int) -> None:
        new_decos = {d["mac"]: d for d in decos}
        new_clients = {c["mac"]: c for c in clients if c["mac"] and c["online"]}
        self._deco_changes(new_decos, now)
        monitored = self._monitored()
        self._client_changes(new_clients, new_decos, monitored, now)
        self.decos, self.clients = new_decos, new_clients
        obs = []
        for d in new_decos.values():
            if d["mac"]:
                obs.append(
                    Observation(
                        now,
                        "deco",
                        d["mac"],
                        ip=d["ip"],
                        info={"is_deco": True, "deco_name": d["name"], "model": d["model"]},
                    )
                )
        for c in new_clients.values():
            deco = new_decos.get(c["deco_mac"])
            obs.append(
                Observation(
                    now,
                    "deco",
                    c["mac"],
                    ip=c["ip"],
                    info={
                        "deco_mac": c["deco_mac"],
                        "deco_name": deco["name"] if deco else None,
                        "connection": c["connection"],
                        "app_name": c["name"],
                    },
                )
            )
        self.inventory.apply(obs)

    def _monitored(self) -> dict[str, str]:
        """MAC → label for devices with a ping target (merged MACs included)."""
        out: dict[str, str] = {}
        rows = self.devices.all()
        by_id = {r["id"]: r for r in rows}
        for r in rows:
            primary = by_id.get(r["merged_into"]) or r
            if primary.get("target_id"):
                out[r["mac"]] = device_label(primary)
        return out

    def _deco_changes(self, new: dict[str, dict], now: int) -> None:
        for mac, old in self.decos.items():
            cur = new.get(mac)
            offline = cur is None or not cur["online"]
            if offline and mac not in self._deco_events:
                self._deco_events[mac] = self.events.open(
                    WIFI,
                    "critical",
                    now,
                    f"Deco {old['name']} offline",
                    details={
                        "deco_mac": mac,
                        "deco": old["name"],
                        "role": old["role"],
                        "type": "deco_offline",
                    },
                )
            if (
                cur is not None
                and cur["online"]
                and old["online"]
                and old["backhaul"] != cur["backhaul"]
            ):
                self.events.instant(
                    WIFI,
                    "warning",
                    now,
                    f"Deco {cur['name']} backhaul changed: {old['backhaul']} → {cur['backhaul']}",
                    details={
                        "deco_mac": mac,
                        "deco": cur["name"],
                        "from": old["backhaul"],
                        "to": cur["backhaul"],
                        "type": "backhaul",
                    },
                )
        for mac, cur in new.items():
            if cur["online"] and mac in self._deco_events:
                self.events.close(self._deco_events.pop(mac), now)
            elif not cur["online"] and mac not in self.decos and mac not in self._deco_events:
                self._deco_events[mac] = self.events.open(
                    WIFI,
                    "critical",
                    now,
                    f"Deco {cur['name']} offline",
                    details={"deco_mac": mac, "deco": cur["name"], "type": "deco_offline"},
                )

    def _client_changes(
        self, new: dict[str, dict], decos: dict[str, dict], monitored: dict[str, str], now: int
    ) -> None:
        def where(c: dict) -> str:
            d = decos.get(c["deco_mac"]) or self.decos.get(c["deco_mac"])
            return f"{d['name'] if d else 'unknown Deco'} ({c['connection']})"

        for mac, label in monitored.items():
            old, cur = self.clients.get(mac), new.get(mac)
            if cur is not None:
                self._gone_since.pop(mac, None)
                if mac in self._wifi_events:
                    self.events.close(self._wifi_events.pop(mac), now, {"back_on": where(cur)})
                if (
                    old is not None
                    and (old["deco_mac"], old["connection"]) != (cur["deco_mac"], cur["connection"])
                    and not cur["wired"]
                ):
                    self.events.instant(
                        WIFI,
                        "info",
                        now,
                        f"{label} roamed: {where(old)} → {where(cur)}",
                        details={"mac": mac, "from": where(old), "to": where(cur), "type": "roam"},
                    )
            elif old is not None or mac in self._gone_since:
                if old is not None:  # missing for the first time
                    self._gone_since[mac] = now
                    self._last_pos[mac] = where(old)
                first = self._gone_since[mac]
                if now - first >= DISCONNECT_AFTER_MS and mac not in self._wifi_events:
                    self._wifi_events[mac] = self.events.open(
                        WIFI,
                        "warning",
                        first,
                        f"{label} disconnected from the Decos",
                        details={
                            "mac": mac,
                            "last_seen_on": self._last_pos.get(mac),
                            "type": "disconnect",
                        },
                    )

    # ── test and status ──────────────────────────────────────────────────────

    async def test(self, host: str, password: str) -> dict:
        client = self.client_factory(host, password)
        try:
            decos, clients = await self._fetch(client)
        except DecoError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            await client.aclose()
        return {
            "ok": True,
            "decos": [
                {
                    "name": d["name"],
                    "role": d["role"],
                    "model": d["model"],
                    "online": d["online"],
                    "backhaul": d["backhaul"],
                }
                for d in decos
            ],
            "clients": len(clients),
        }

    def status(self) -> dict:
        cfg = self.config()
        return {
            **cfg,
            "password_set": self.store.kv_get(PASSWORD_KEY, None) is not None,
            "last_poll_ms": self.last_poll_ms,
            "last_ok_ms": self.last_ok_ms,
            "error": self.error,
            "decos": list(self.decos.values()),
            "clients": len(self.clients),
        }
