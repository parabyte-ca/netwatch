"""Home Hub 4000 integration: reads the Hub's own view of the Bell line every minute.

Records uptime (to catch restarts), fibre optical levels and link state, and the internet
(PPP) session, and turns changes into timeline events the diagnosis uses as hard evidence:
"the Hub restarted", "the fibre link went down", "Bell ended the internet session".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable

from sqlalchemy import Engine, delete, insert, select

from ...db import hub_stats
from ..events import HUB, EventLog
from ..secrets import SecretBox
from ..storage import Store
from .client import HubClient, HubError, HubLoginError

log = logging.getLogger(__name__)

CONFIG_KEY = "hub_config"
PASSWORD_KEY = "hub_password"  # noqa: S105 - the kv key, not a password
POLL_S = 60
MAX_LOGIN_FAILURES = 3
WEAK_RX_DBM = -27.0  # GPON receivers typically give up around -28 dBm
WEAK_COOLDOWN_MS = 6 * 3600_000

ClientFactory = Callable[[str, str], HubClient]


def _up(status: str | None) -> bool | None:
    if not status:
        return None
    return str(status).lower() in ("up", "connected", "enabled", "o5", "operation")


class HubStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def add(self, ts_ms: int, snap: dict) -> None:
        opt = snap.get("optical") or {}
        with self.engine.begin() as conn:
            conn.execute(
                insert(hub_stats).values(
                    ts_ms=ts_ms,
                    uptime_s=snap.get("uptime_s"),
                    wan_up=snap.get("wan_up"),
                    rx_dbm=opt.get("rx_dbm"),
                    tx_dbm=opt.get("tx_dbm"),
                    details=json.dumps(
                        {"ppp": snap.get("ppp"), "optical_status": opt.get("status")}
                    ),
                )
            )

    def history(self, start_ms: int, end_ms: int, limit: int = 5000) -> list[dict]:
        stmt = (
            select(hub_stats)
            .where(hub_stats.c.ts_ms >= start_ms, hub_stats.c.ts_ms < end_ms)
            .order_by(hub_stats.c.ts_ms)
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [
                {**dict(r._mapping), "details": json.loads(r.details or "{}")}
                for r in conn.execute(stmt)
            ]

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(hub_stats).where(hub_stats.c.ts_ms < cutoff_ms)).rowcount


class HubService:
    def __init__(
        self,
        store: Store,
        events: EventLog,
        hub_store: HubStore,
        secrets: SecretBox,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.store = store
        self.events = events
        self.hub_store = hub_store
        self.secrets = secrets
        self.client_factory = client_factory or (lambda host, pw: HubClient(host, pw))
        self.last: dict | None = None
        self.last_poll_ms: int | None = None
        self.last_ok_ms: int | None = None
        self.error: str | None = None
        self.paused: str | None = None
        self._failures = 0
        self._client: HubClient | None = None
        self._client_key: tuple[str, str] | None = None
        self._task: asyncio.Task | None = None
        self._down_event: int | None = None  # open "fibre/WAN down" event
        self._last_weak_ms = 0

    # ── configuration ────────────────────────────────────────────────────────

    def config(self) -> dict:
        c = self.store.kv_get(CONFIG_KEY, {}) or {}
        return {"host": c.get("host", ""), "enabled": bool(c.get("enabled", False))}

    def password(self) -> str | None:
        token = self.store.kv_get(PASSWORD_KEY, None)
        return self.secrets.decrypt(token) if token else None

    @property
    def configured(self) -> bool:
        return bool(self.config()["host"]) and self.store.kv_get(PASSWORD_KEY, None) is not None

    def save(self, host: str, enabled: bool, password: str | None) -> None:
        host = host.strip()
        if host != self.config()["host"] and not password:
            self.store.kv_set(PASSWORD_KEY, None)  # never reuse a password for another host
        self.store.kv_set(CONFIG_KEY, {"host": host, "enabled": enabled})
        if password:
            self.store.kv_set(PASSWORD_KEY, self.secrets.encrypt(password))
        self._failures = 0
        self.paused = None

    def forget_password(self) -> None:
        self.store.kv_set(PASSWORD_KEY, None)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-hub")

    def kick(self) -> None:
        if self.config()["enabled"] and self.configured and not self.paused:
            self._kick = asyncio.create_task(self.poll(), name="netwatch-hub-kick")

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
        await asyncio.sleep(10)
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
                log.exception("Hub poll failed")
            await asyncio.sleep(POLL_S)

    # ── polling ──────────────────────────────────────────────────────────────

    async def _ensure_client(self) -> HubClient | None:
        cfg = await asyncio.to_thread(self.config)
        pw = await asyncio.to_thread(self.password)
        if not (cfg["host"] and pw):
            return None
        if self._client_key != (cfg["host"], pw):
            await self._drop()
            self._client = self.client_factory(cfg["host"], pw)
            self._client_key = (cfg["host"], pw)
        return self._client

    async def exposure(self) -> dict | None:
        """Port forwards, UPnP and DMZ from the Hub; None when the Hub isn't set up or reachable."""
        cfg = await asyncio.to_thread(self.config)
        configured = await asyncio.to_thread(lambda: self.configured)
        if not (cfg["enabled"] and configured) or self.paused:
            return None
        client = await self._ensure_client()
        if client is None:
            return None
        try:
            return await client.exposure()
        except HubError as exc:
            log.warning("Hub exposure read failed", extra={"error": str(exc)})
            return None

    async def poll(self, now_ms: int | None = None) -> bool:
        now = now_ms or int(time.time() * 1000)
        self.last_poll_ms = now
        if await asyncio.to_thread(self.password) is None:
            self.error = "the stored password cannot be decrypted; enter it again"
            return False
        await self._ensure_client()
        assert self._client is not None  # noqa: S101 - host and password are set
        try:
            snap = await self._client.snapshot()
        except HubLoginError as exc:
            self.error = str(exc)
            self._failures += 1
            if self._failures >= MAX_LOGIN_FAILURES or "refusing" in str(exc):
                self.paused = f"{self._failures} failed sign-ins"
            await self._drop()
            return False
        except HubError as exc:
            self.error = str(exc)
            log.warning("Hub poll failed", extra={"error": self.error})
            return False
        self._failures = 0
        self.error = None
        self.last_ok_ms = now
        opt, ppp = snap.get("optical") or {}, snap.get("ppp") or {}
        states = [s for s in (_up(opt.get("status")), _up(ppp.get("status"))) if s is not None]
        snap["wan_up"] = all(states) if states else None
        await asyncio.to_thread(self._apply, snap, now)
        return True

    def _apply(self, snap: dict, now: int) -> None:
        prev, self.last = self.last, snap
        self.hub_store.add(now, snap)
        up, opt, ppp = snap.get("uptime_s"), snap.get("optical") or {}, snap.get("ppp") or {}
        p_up = prev.get("uptime_s") if prev else None
        # A restart: uptime went backwards (or NetWatch started right after one).
        if up is not None and (
            (p_up is not None and up < p_up) or (prev is None and up < 2 * POLL_S)
        ):
            started = now - up * 1000
            was = f" after {_dur(p_up)}" if p_up else ""
            self.events.instant(
                HUB,
                "warning",
                started,
                f"Home Hub restarted{was}",
                details={"type": "hub_restart", "uptime_s": up, "previous_uptime_s": p_up},
            )
        # The internet session ended and came back while the Hub itself stayed up.
        pp_up = (prev.get("ppp") or {}).get("uptime_s") if prev else None
        if (
            ppp.get("uptime_s") is not None
            and pp_up is not None
            and up
            and p_up
            and up >= p_up
            and ppp["uptime_s"] < pp_up
        ):
            self.events.instant(
                HUB,
                "warning",
                now - ppp["uptime_s"] * 1000,
                "Bell internet session reconnected (the Hub stayed up)",
                details={
                    "type": "ppp_reconnect",
                    "last_error": ppp.get("last_error"),
                    "previous_session_s": pp_up,
                },
            )
        # Fibre / WAN down and back.
        if snap.get("wan_up") is False and self._down_event is None:
            what = "Fibre link" if _up(opt.get("status")) is False else "Internet connection"
            self._down_event = self.events.open(
                HUB,
                "critical",
                now,
                f"{what} down (reported by the Home Hub)",
                details={
                    "type": "wan_down",
                    "optical_status": opt.get("status"),
                    "ppp_status": ppp.get("status"),
                    "last_error": ppp.get("last_error"),
                },
            )
        elif snap.get("wan_up") and self._down_event is not None:
            self.events.close(self._down_event, now)
            self._down_event = None
        rx = opt.get("rx_dbm")
        if rx is not None and rx < WEAK_RX_DBM and now - self._last_weak_ms > WEAK_COOLDOWN_MS:
            self._last_weak_ms = now
            self.events.instant(
                HUB,
                "warning",
                now,
                f"Weak fibre signal: {rx} dBm",
                details={"type": "weak_signal", "rx_dbm": rx, "threshold_dbm": WEAK_RX_DBM},
            )

    # ── test, explore, status ────────────────────────────────────────────────

    async def test(self, host: str, password: str) -> dict:
        client = self.client_factory(host, password)
        try:
            snap = await client.snapshot()
        except HubError as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            await client.aclose()
        return {"ok": True, "snapshot": snap, "method": client.method}

    async def explore(self, xpath: str) -> object:
        cfg = await asyncio.to_thread(self.config)
        pw = await asyncio.to_thread(self.password)
        if not (cfg["host"] and pw):
            raise HubError("save the Hub's address and password first")
        client = self.client_factory(cfg["host"], pw)
        try:
            return await client.explore(xpath)
        finally:
            await client.aclose()

    def status(self) -> dict:
        return {
            **self.config(),
            "password_set": self.store.kv_get(PASSWORD_KEY, None) is not None,
            "last_poll_ms": self.last_poll_ms,
            "last_ok_ms": self.last_ok_ms,
            "error": self.error,
            "paused": self.paused,
            "snapshot": self.last,
        }


def _dur(s: int | None) -> str:
    if not s:
        return "?"
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    return f"{d} d {h} h" if d else f"{h} h {rem // 60} min"
