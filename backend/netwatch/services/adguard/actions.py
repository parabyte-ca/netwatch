"""Opt-in changes to AdGuard Home: pause protection, allow or block a domain, switch upstream DNS.

Everything here refuses to run unless "AdGuard Home actions" is turned on (Admin → settings),
except ending a pause: once protection was paused from NetWatch, turning it back on when the
time is up always happens, also after a restart (the end time is kept in the database).

Custom rules are read-modify-write (AdGuard only accepts the whole list): NetWatch appends or
removes only its own exact lines and keeps a list of them, so it never touches your own rules.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from collections.abc import Callable

from ..events import SECURITY
from .client import AdGuardError

log = logging.getLogger(__name__)

PAUSE_KEY = "adguard_pause"
RULES_KEY = "adguard_rules"
PREV_UPSTREAMS_KEY = "adguard_prev_upstreams"
PAUSE_MIN, PAUSE_MAX = 1, 1440  # minutes
MAX_RULES = 200
MAX_UPSTREAMS = 5

UPSTREAM_PRESETS: tuple[tuple[str, list[str]], ...] = (
    ("Cloudflare (encrypted, DNS-over-TLS)", ["tls://1.1.1.1", "tls://1.0.0.1"]),
    ("Quad9 (encrypted, DNS-over-TLS)", ["tls://dns.quad9.net"]),
    ("Google (encrypted, DNS-over-TLS)", ["tls://dns.google"]),
    ("Cloudflare (plain)", ["1.1.1.1", "1.0.0.1"]),
    ("Quad9 (plain)", ["9.9.9.9", "149.112.112.112"]),
    ("Google (plain)", ["8.8.8.8", "8.8.4.4"]),
)

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_UPSTREAM_URL = re.compile(r"^(tls|https|quic|h3|tcp|udp|sdns)://[^\s]{1,190}$")


class ActionsDisabled(Exception):
    """The "AdGuard Home actions" setting is off."""


class ActionError(Exception):
    """An action that cannot be done; the message is safe to show."""


def clean_domain(raw: str) -> str:
    """A plain domain name (IDNA), or ActionError. No wildcards, paths, ports or addresses."""
    d = (raw or "").strip().lower().rstrip(".")
    if not d or len(d) > 253 or any(c in d for c in "*/:@?#|^$ \t\\"):
        raise ActionError("enter a plain domain name, such as example.com")
    try:
        ipaddress.ip_address(d)
    except ValueError:
        pass
    else:
        raise ActionError("enter a domain name, not an address")
    try:
        d = d.encode("idna").decode("ascii")
    except UnicodeError:
        raise ActionError("that is not a valid domain name") from None
    labels = d.split(".")
    if len(labels) < 2 or not all(_LABEL.match(x) for x in labels) or labels[-1].isdigit():
        raise ActionError("that is not a valid domain name")
    return d


def rule_for(domain: str, kind: str) -> str:
    return f"@@||{domain}^" if kind == "allow" else f"||{domain}^"


def clean_upstreams(raw: list[str]) -> list[str]:
    out: list[str] = []
    for u in raw or []:
        u = str(u).strip()
        if not u or u in out:
            continue
        if not _UPSTREAM_URL.match(u) and not _plain_address(u):
            raise ActionError(
                f"{u[:60]!r} is not an upstream AdGuard Home accepts "
                "(e.g. tls://1.1.1.1, https://dns.quad9.net/dns-query or 9.9.9.9)"
            )
        out.append(u)
    if not 1 <= len(out) <= MAX_UPSTREAMS:
        raise ActionError(f"give 1 to {MAX_UPSTREAMS} upstream servers")
    return out


def _plain_address(u: str) -> bool:
    """An IP address, optionally with a port (``9.9.9.9:53``, ``[2620:fe::fe]:53``)."""
    host, port = u, None
    if u.startswith("["):
        host, _, rest = u[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else (None if not rest else "x")
    elif u.count(":") == 1:
        host, port = u.split(":")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return port is None or (port.isdigit() and 0 < int(port) < 65536)


class AdGuardActions:
    def __init__(self, service, settings, *, gateway_ip: Callable[[], str | None] = lambda: None):
        self.service = service  # AdGuardService: its client, its config, its kv store
        self.settings = settings
        self.gateway_ip = gateway_ip
        self.store = service.store
        self.events = service.events
        self._lock = asyncio.Lock()

    # ── state ────────────────────────────────────────────────────────────────

    def enabled(self) -> bool:
        return bool(getattr(self.settings, "adguard_actions", False))

    def pause_state(self, now_ms: int | None = None) -> dict | None:
        p = self.store.kv_get(PAUSE_KEY, None)
        now_ms = now_ms or int(time.time() * 1000)
        return p if isinstance(p, dict) and p.get("until_ms", 0) > now_ms else None

    def rules(self) -> list[dict]:
        r = self.store.kv_get(RULES_KEY, []) or []
        return r if isinstance(r, list) else []

    def state(self) -> dict:
        prev = self.store.kv_get(PREV_UPSTREAMS_KEY, None)
        return {
            "enabled": self.enabled(),
            "pause": self.pause_state(),
            "rules": self.rules(),
            "previous_upstreams": prev if isinstance(prev, dict) else None,
            "presets": [{"name": n, "upstreams": u} for n, u in UPSTREAM_PRESETS],
        }

    async def _client(self):
        if not self.enabled():
            raise ActionsDisabled("AdGuard Home actions are turned off (Admin → settings)")
        client = await self.service._ensure_client()
        if client is None:
            raise ActionError("set up AdGuard Home under Admin first")
        return client

    async def _call(self, coro):
        try:
            return await coro
        except AdGuardError as exc:
            raise ActionError(str(exc)) from None

    # ── protection ───────────────────────────────────────────────────────────

    async def pause(self, minutes: int, by: str) -> dict:
        if not PAUSE_MIN <= int(minutes) <= PAUSE_MAX:
            raise ActionError(f"pause for {PAUSE_MIN} to {PAUSE_MAX} minutes")
        async with self._lock:
            client = await self._client()
            await self._call(client.set_protection(False, int(minutes) * 60))
            now = int(time.time() * 1000)
            p = {
                "until_ms": now + int(minutes) * 60_000,
                "started_ms": now,
                "minutes": int(minutes),
                "by": by,
            }
            await asyncio.to_thread(self.store.kv_set, PAUSE_KEY, p)
            return p

    async def resume(self, by: str) -> None:
        async with self._lock:
            client = await self._client()
            await self._call(client.set_protection(True))
            await asyncio.to_thread(self.store.kv_set, PAUSE_KEY, None)

    async def tick(self, now_ms: int | None = None) -> bool:
        """End a pause started from NetWatch once its time is up (also after a restart, or
        when AdGuard ignored the duration). Runs even with actions turned off since: it only
        puts protection back. True when protection was turned back on here."""
        now_ms = now_ms or int(time.time() * 1000)
        p = await asyncio.to_thread(self.store.kv_get, PAUSE_KEY, None)
        if not isinstance(p, dict) or p.get("until_ms", 0) > now_ms:
            return False
        client = await self.service._ensure_client()
        if client is None:
            return False
        try:
            await client.set_protection(True)
        except AdGuardError as exc:
            log.warning("could not resume AdGuard Home protection", extra={"error": str(exc)})
            return False  # retried on the next tick
        await asyncio.to_thread(self.store.kv_set, PAUSE_KEY, None)
        await asyncio.to_thread(
            self.events.instant, SECURITY, "info", now_ms,
            "AdGuard Home protection turned back on: the pause from NetWatch ended",
            details={"by": "NetWatch", "action": "adguard_resume"},
        )  # fmt: skip
        return True

    # ── custom rules ─────────────────────────────────────────────────────────

    async def add_rule(self, domain: str, kind: str, by: str) -> dict:
        if kind not in ("allow", "block"):
            raise ActionError("kind must be allow or block")
        d = clean_domain(domain)
        cfg = self.service.config()
        if d in {cfg.get("host", "").lower(), (self.gateway_ip() or "").lower()}:
            raise ActionError("that is the address of AdGuard Home or the gateway")
        rule, other = rule_for(d, kind), rule_for(d, "block" if kind == "allow" else "allow")
        async with self._lock:
            client = await self._client()
            current = await self._call(client.user_rules())
            ours = self.rules()
            mine = {r["rule"] for r in ours}
            if rule in current and rule not in mine:
                raise ActionError(f"{rule} is already one of your own rules in AdGuard Home")
            if len(ours) >= MAX_RULES:
                raise ActionError("NetWatch already added 200 rules; remove some first")
            # Allowing what NetWatch blocked (or the reverse) replaces NetWatch's own rule.
            new = [r for r in current if not (r == other and other in mine)]
            if rule not in new:
                new.append(rule)
            if new != current:
                await self._call(client.set_user_rules(new))
            ours = [r for r in ours if r["rule"] not in (rule, other)]
            entry = {"rule": rule, "domain": d, "kind": kind, "added_ms": int(time.time() * 1000),
                     "by": by}  # fmt: skip
            ours.append(entry)
            await asyncio.to_thread(self.store.kv_set, RULES_KEY, ours)
            return entry

    async def remove_rule(self, rule: str) -> dict:
        async with self._lock:
            ours = self.rules()
            entry = next((r for r in ours if r["rule"] == rule), None)
            if entry is None:
                raise ActionError("NetWatch only removes rules it added")
            client = await self._client()
            current = await self._call(client.user_rules())
            if rule in current:
                await self._call(client.set_user_rules([r for r in current if r != rule]))
            await asyncio.to_thread(
                self.store.kv_set, RULES_KEY, [r for r in ours if r["rule"] != rule]
            )
            return entry

    # ── upstream DNS ─────────────────────────────────────────────────────────

    async def test_upstreams(self, upstreams: list[str]) -> dict:
        ups = clean_upstreams(upstreams)
        client = await self._client()
        current = await self._call(client.upstreams())
        results = await self._call(client.test_upstreams(ups, current["bootstrap_dns"]))
        return {"upstreams": ups, "results": results,
                "ok": all(v == "OK" for v in results.values())}  # fmt: skip

    async def current_upstreams(self) -> list[str]:
        client = await self._client()
        return (await self._call(client.upstreams()))["upstream_dns"]

    async def switch_upstreams(self, upstreams: list[str]) -> dict:
        async with self._lock:
            test = await self.test_upstreams(upstreams)
            if not test["ok"]:
                bad = [f"{u}: {r}" for u, r in test["results"].items() if r != "OK"]
                raise ActionError("AdGuard Home could not use " + "; ".join(bad))
            client = await self._client()
            before = (await self._call(client.upstreams()))["upstream_dns"]
            await self._call(client.set_upstreams(test["upstreams"]))
            if before != test["upstreams"]:
                saved = {"upstreams": before, "saved_ms": int(time.time() * 1000)}
                await asyncio.to_thread(self.store.kv_set, PREV_UPSTREAMS_KEY, saved)
            return {"previous": before, "current": test["upstreams"]}

    async def restore_upstreams(self) -> dict:
        prev = self.store.kv_get(PREV_UPSTREAMS_KEY, None)
        if not isinstance(prev, dict) or not prev.get("upstreams"):
            raise ActionError("there is no previous upstream list to switch back to")
        return await self.switch_upstreams(prev["upstreams"])
