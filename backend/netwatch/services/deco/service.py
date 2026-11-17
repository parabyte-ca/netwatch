"""Deco mesh integration: polls the Decos for their units and connected clients, keeps the
device inventory informed (which Deco, band, wired or Wi-Fi, app names) and records Wi-Fi
events: Deco offline/online, backhaul changes, and roaming / disconnects of monitored devices.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable

from ..discovery.inventory import DeviceStore, Inventory, device_label
from ..discovery.packets import Observation, clean_name
from ..events import SECURITY, WIFI, EventLog
from ..poll_state import poll_state
from ..secrets import SecretBox
from ..storage import Store
from ..traffic import TrafficStore
from .client import DecoClient, DecoError, DecoKeyChanged, DecoLoginError, DecoNoAnswer

log = logging.getLogger(__name__)

CONFIG_KEY = "deco_config"
PASSWORD_KEY = "deco_password"  # noqa: S105 - the kv key, not a password
PIN_KEY = "deco_key_pin"  # {"host", "fp"}: the trusted sign-in key (see DecoClient)
POLL_S = 30
MAX_LOGIN_FAILURES = 3  # then stop, so a wrong password cannot lock the owner account out
_MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
DISCONNECT_AFTER_MS = 60_000
REBOOT_KEY = "deco_reboot_last"  # {mac|"mesh": ms of the last restart NetWatch asked for}
REBOOT_COOLDOWN_MS = 5 * 60_000  # one Deco
MESH_COOLDOWN_MS = 15 * 60_000  # the whole mesh
REBOOT_GAP_S = 10  # between Decos in a mesh restart
REBOOT_WINDOW_MS = 15 * 60_000  # an outage this soon after our request is the planned restart
UNCONFIRMED_MS = 3 * 60_000  # no answer to the request: it must have gone offline by then


class DecoActionError(Exception):
    """A restart that cannot or must not happen; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


BAND_LABEL = {
    "band2_4": "2.4 GHz",
    "band5": "5 GHz",
    "band5_2": "5 GHz-2",
    "band6": "6 GHz",
    "wired": "wired",
}


def norm_mac(mac: str | None) -> str:
    """Canonical MAC, or "" when the value is not a MAC (the Deco's data is not trusted)."""
    m = str(mac or "").strip().lower().replace("-", ":")
    return m if _MAC.match(m) else ""


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
    name = clean_name(d.get("custom_nickname")) or clean_name(_title(d.get("nickname"))) or mac[-5:]
    return {
        "mac": mac,
        "raw_mac": d.get("mac") if mac else None,  # the Deco's format, to query its clients
        "name": name,
        "ip": clean_name(d.get("device_ip"), 45),
        "model": clean_name(d.get("device_model"), 32),
        "hardware": clean_name(d.get("hardware_ver"), 32),
        "firmware": clean_name(d.get("software_ver"), 64),
        "role": "main" if d.get("role") == "master" else "satellite",
        "online": d.get("group_status") in (None, "connected"),
        "internet": d.get("inet_status"),
        "backhaul": clean_name(_backhaul(d.get("connection_type")), 40) or "unknown",
        "signal": d.get("signal_level") or {},
        "backhaul_speed": d.get("backhual_speed"),
    }


def _num(v) -> float:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return f if 0 <= f < 1e9 else 0.0


def normalize_client(c: dict, deco_mac: str) -> dict:
    conn = (clean_name(str(c.get("connection_type") or ""), 20) or "").lower()
    ip = clean_name(c.get("ip"), 45)
    return {
        "mac": norm_mac(c.get("mac")),
        "name": clean_name(c.get("name")),
        "ip": ip if ip not in (None, "0.0.0.0", "UNKNOWN") else None,
        "online": bool(c.get("online", True)),
        "connection": BAND_LABEL.get(conn, conn or "unknown"),
        "wired": conn == "wired",
        "guest": c.get("interface") == "guest",
        "deco_mac": deco_mac,
        # The Deco reports current speeds in kilobytes per second.
        "down_bps": _num(c.get("down_speed")) * 8000,
        "up_bps": _num(c.get("up_speed")) * 8000,
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
        traffic: TrafficStore | None = None,
    ) -> None:
        self.store = store
        self.events = events
        self.devices = devices
        self.inventory = inventory
        self.secrets = secrets
        self.traffic = traffic
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
        self._login_failures = 0
        self.rebooting: dict[str, int] = {}  # deco mac → when NetWatch asked it to restart
        self._unconfirmed: dict[
            str, int
        ] = {}  # restart sent, no reply: waiting to see it go offline
        self._planned: set[str] = set()  # deco macs whose open outage event is a planned restart
        self._sleep = asyncio.sleep
        self.paused: str | None = None  # why polling stopped until the settings are saved

    # ── configuration ────────────────────────────────────────────────────────

    def config(self) -> dict:
        c = self.store.kv_get(CONFIG_KEY, {}) or {}
        return {"host": c.get("host", ""), "enabled": bool(c.get("enabled", False))}

    def password(self) -> str | None:
        token = self.store.kv_get(PASSWORD_KEY, None)
        return self.secrets.decrypt(token) if token else None

    def save(self, host: str, enabled: bool, password: str | None) -> None:
        host = host.strip()
        if host != self.config()["host"]:
            # A password and key trusted for one address are never used for another.
            self.store.kv_set(PIN_KEY, None)
            if not password:
                self.store.kv_set(PASSWORD_KEY, None)
        self.store.kv_set(CONFIG_KEY, {"host": host, "enabled": enabled})
        if password:
            self.store.kv_set(PASSWORD_KEY, self.secrets.encrypt(password))
        self._login_failures = 0
        self.paused = None

    def forget_password(self) -> None:
        self.store.kv_set(PASSWORD_KEY, None)

    def pin(self, host: str) -> str | None:
        p = self.store.kv_get(PIN_KEY, None) or {}
        return p.get("fp") if p.get("host") == host else None

    def trust_key(self, host: str, fp: str) -> None:
        self.store.kv_set(PIN_KEY, {"host": host, "fp": fp})
        self._login_failures = 0
        self.paused = None

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

    def set_polling(self, enabled: bool) -> None:
        """Turn polling on or off without touching the address or the password."""
        c = self.store.kv_get(CONFIG_KEY, {}) or {}
        self.store.kv_set(CONFIG_KEY, {**c, "enabled": bool(enabled)})

    def kick(self) -> None:
        """Poll now (after the settings change) instead of waiting for the next cycle."""
        if self.config()["enabled"] and self.configured and not self.paused:
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
                if self.config()["enabled"] and self.configured and not self.paused:
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
        pin = self.pin(cfg["host"])
        self._client.key_pin = pin
        try:
            decos, clients = await self._fetch(self._client)
        except DecoKeyChanged as exc:
            self.error = str(exc)
            self.paused = "the Deco's sign-in key changed"
            await asyncio.to_thread(
                self.events.instant, SECURITY, "critical", now,
                "Deco sign-in key changed: password withheld, polling stopped",
                details={"host": cfg["host"]},
            )  # fmt: skip
            return False
        except DecoLoginError as exc:
            self.error = str(exc)
            self._login_failures += 1
            if self._login_failures >= MAX_LOGIN_FAILURES:
                self.paused = f"{self._login_failures} failed sign-ins"
            log.warning("Deco sign-in failed", extra={"error": self.error})
            return False
        except DecoError as exc:
            self.error = str(exc)
            log.warning("Deco poll failed", extra={"error": self.error})
            return False
        self._login_failures = 0
        if pin is None and self._client.key_seen:
            await asyncio.to_thread(self.trust_key, cfg["host"], self._client.key_seen)
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
        self._check_unconfirmed(now)
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
        if self.traffic is not None:
            self.traffic.record(
                {c["mac"]: (c["down_bps"], c["up_bps"]) for c in new_clients.values()}, now
            )

    def _monitored(self) -> dict[str, tuple[str, str | None]]:
        """MAC → (label, type) for devices with a ping target (merged MACs included)."""
        out: dict[str, tuple[str, str | None]] = {}
        rows = self.devices.all()
        by_id = {r["id"]: r for r in rows}
        for r in rows:
            primary = by_id.get(r["merged_into"]) or r
            if primary.get("target_id"):
                out[r["mac"]] = (
                    device_label(primary),
                    primary.get("type_override") or primary.get("type"),
                )
        return out

    def _deco_changes(self, new: dict[str, dict], now: int) -> None:
        for mac, old in self.decos.items():
            cur = new.get(mac)
            offline = cur is None or not cur["online"]
            if offline and mac not in self._deco_events:
                if self._planned_restart(mac, now):
                    self._unconfirmed.pop(mac, None)
                    self._deco_events[mac] = self.events.open(
                        WIFI,
                        "info",
                        now,
                        f"Deco {old['name']} restarted from NetWatch",
                        details={"deco_mac": mac, "deco": old["name"], "type": "reboot"},
                    )
                    self._planned.add(mac)
                else:
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
                extra = None
                if mac in self._planned:
                    self._planned.discard(mac)
                    started = self.rebooting.pop(mac, now)
                    extra = {"back_after_s": max(0, (now - started) // 1000)}
                self.events.close(self._deco_events.pop(mac), now, extra)
            elif not cur["online"] and mac not in self.decos and mac not in self._deco_events:
                self._deco_events[mac] = self.events.open(
                    WIFI,
                    "critical",
                    now,
                    f"Deco {cur['name']} offline",
                    details={"deco_mac": mac, "deco": cur["name"], "type": "deco_offline"},
                )

    def _client_changes(
        self,
        new: dict[str, dict],
        decos: dict[str, dict],
        monitored: dict[str, tuple[str, str | None]],
        now: int,
    ) -> None:
        def where(c: dict) -> str:
            d = decos.get(c["deco_mac"]) or self.decos.get(c["deco_mac"])
            return f"{d['name'] if d else 'unknown Deco'} ({c['connection']})"

        for mac, (label, kind) in monitored.items():
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
                    # A laptop going quiet is usually switched off or carried out of the house.
                    away = kind == "laptop"
                    self._wifi_events[mac] = self.events.open(
                        WIFI,
                        "info" if away else "warning",
                        first,
                        f"{label} left the network (away or switched off)"
                        if away
                        else f"{label} disconnected from the Decos",
                        details={
                            "mac": mac,
                            "last_seen_on": self._last_pos.get(mac),
                            "type": "away" if away else "disconnect",
                        },
                    )

    # ── test and status ──────────────────────────────────────────────────────

    # ── actions (opt-in, see the deco_actions setting) ───────────────────────

    def _planned_restart(self, mac: str, now: int) -> bool:
        started = self.rebooting.get(mac)
        if started is None:
            return False
        if now - started > REBOOT_WINDOW_MS:
            self.rebooting.pop(mac, None)
            return False
        return True

    def _check_cooldown(self, key: str, window_ms: int, now: int, what: str) -> None:
        last = (self.store.kv_get(REBOOT_KEY, {}) or {}).get(key)
        if isinstance(last, int) and now - last < window_ms:
            wait = (window_ms - (now - last) + 59_999) // 60_000
            raise DecoActionError(429, f"{what} was restarted a moment ago; wait {wait} min")

    def _remember(self, key: str, now: int) -> None:
        last = dict(self.store.kv_get(REBOOT_KEY, {}) or {})
        last[key] = now
        self.store.kv_set(REBOOT_KEY, last)

    async def _restart(self, decos: list[dict], now: int) -> bool:
        """Ask the Decos to restart. False when the Deco went quiet without a reply (usual for a
        restart): it is then watched until it goes offline."""
        cfg = self.config()
        pw = self.password()
        if not pw or not cfg["host"]:
            raise DecoActionError(422, "the Deco is not set up, or its password cannot be read")
        client = self.client_factory(cfg["host"], pw)
        client.key_pin = await asyncio.to_thread(self.pin, cfg["host"])
        replied = True
        try:
            await client.reboot([d["raw_mac"] or d["mac"] for d in decos])
        except DecoNoAnswer:
            replied = False
        except DecoError as exc:
            raise DecoActionError(422, f"the Deco did not restart: {exc}") from exc
        finally:
            await client.aclose()
        for d in decos:
            self.rebooting[d["mac"]] = now
            if not replied:
                self._unconfirmed[d["mac"]] = now
        return replied

    def _check_unconfirmed(self, now: int) -> None:
        """A restart nobody answered must show as the Deco going offline; if not, say so."""
        for mac, sent in list(self._unconfirmed.items()):
            if now - sent < UNCONFIRMED_MS:
                continue
            self._unconfirmed.pop(mac, None)
            self.rebooting.pop(mac, None)
            name = (self.decos.get(mac) or {}).get("name") or mac
            self.events.instant(
                WIFI,
                "warning",
                now,
                f"Deco {name} did not restart (no reply to the request, and it never went offline)",
                details={"deco_mac": mac, "deco": name, "type": "reboot_failed"},
            )

    async def reboot(self, mac: str, now_ms: int | None = None) -> dict:
        """Restart one Deco. Raises DecoActionError (404 unknown, 409 offline, 429 too soon)."""
        now = now_ms or int(time.time() * 1000)
        deco = self.decos.get(norm_mac(mac))
        if deco is None:
            raise DecoActionError(404, "no such Deco")
        if not deco["online"]:
            raise DecoActionError(409, f"{deco['name']} is offline, so it cannot be restarted")
        self._check_cooldown(deco["mac"], REBOOT_COOLDOWN_MS, now, deco["name"])
        replied = await self._restart([deco], now)
        self._remember(deco["mac"], now)
        return {
            "started_ms": now,
            "decos": [deco["name"]],
            "unconfirmed": [] if replied else [deco["name"]],
        }

    async def reboot_mesh(self, now_ms: int | None = None) -> dict:
        """Restart every online Deco, satellites first and the main Deco last."""
        now = now_ms or int(time.time() * 1000)
        online = [d for d in self.decos.values() if d["online"]]
        if not online:
            raise DecoActionError(409, "no Deco is online")
        self._check_cooldown("mesh", MESH_COOLDOWN_MS, now, "The Wi-Fi network")
        order = sorted(online, key=lambda d: d["role"] == "main")  # False (satellite) first
        done: list[str] = []
        quiet: list[str] = []
        for i, d in enumerate(order):
            if i:
                await self._sleep(REBOOT_GAP_S)
            try:
                replied = await self._restart(
                    [d], int(time.time() * 1000) if now_ms is None else now
                )
            except DecoActionError as exc:
                note = f"; already restarted: {', '.join(done)}" if done else ""
                raise DecoActionError(exc.status, f"{d['name']}: {exc}{note}") from exc
            done.append(d["name"])
            if not replied:
                quiet.append(d["name"])
        self._remember("mesh", now)
        for d in order:
            self._remember(d["mac"], now)
        return {"started_ms": now, "decos": done, "unconfirmed": quiet}

    async def test(self, host: str, password: str, *, trust: bool = False) -> dict:
        """Try a sign-in. With ``trust`` (the owner typed the password for the saved Deco), a
        new or changed sign-in key is accepted and pinned; otherwise the pinned key is
        enforced."""
        client = self.client_factory(host, password)
        client.key_pin = None if trust else await asyncio.to_thread(self.pin, host)
        try:
            decos, clients = await self._fetch(client)
        except DecoError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            await client.aclose()
        if trust and client.key_seen:
            await asyncio.to_thread(self.trust_key, host, client.key_seen)
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

    def state(self) -> str:
        return poll_state(
            configured=self.configured,
            enabled=self.config()["enabled"],
            paused=self.paused,
            last_ok_ms=self.last_ok_ms,
        )

    def status(self) -> dict:
        cfg = self.config()
        return {
            **cfg,
            "password_set": self.store.kv_get(PASSWORD_KEY, None) is not None,
            "last_poll_ms": self.last_poll_ms,
            "last_ok_ms": self.last_ok_ms,
            "error": self.error,
            "paused": self.paused,
            "state": self.state(),
            "decos": list(self.decos.values()),
            "clients": len(self.clients),
        }
