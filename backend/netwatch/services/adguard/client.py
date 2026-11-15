"""Client for the AdGuard Home REST API (``/control/*``, HTTP basic auth).

Monitoring only sends GET requests: status, statistics, the query log, clients and DHCP. The few
write methods at the end (pause protection, custom rules, upstream DNS) are only called by
``AdGuardActions``, which refuses unless "AdGuard Home actions" is turned on. Every failure
becomes an ``AdGuardError`` whose message is safe to show (never the password or the
Authorization header).
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Any

import httpx

SECRET_KEY = re.compile(r"pass|secret|token|credential|key", re.I)
MAX_PAGE = 500  # entries per query-log request (AdGuard caps it too)
MAC_RE = re.compile(r"^([0-9a-f]{2})[:-]?([0-9a-f]{2})[:-]?([0-9a-f]{2})[:-]?"
                    r"([0-9a-f]{2})[:-]?([0-9a-f]{2})[:-]?([0-9a-f]{2})$", re.I)  # fmt: skip


class AdGuardError(Exception):
    """Any failure talking to AdGuard Home; the message is safe to show."""


class AdGuardLoginError(AdGuardError):
    pass


def redact(value: Any) -> Any:
    """Hide anything that looks like a credential in a raw API answer."""
    if isinstance(value, dict):
        return {
            k: "••••"
            if SECRET_KEY.search(str(k)) and isinstance(v, str | int) and v != ""
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def norm_mac(raw: Any) -> str | None:
    """``AA-BB-CC-DD-EE-FF`` and friends as ``aa:bb:cc:dd:ee:ff``; None if it isn't a MAC."""
    m = MAC_RE.match(str(raw or "").strip())
    return ":".join(m.groups()).lower() if m else None


def id_kind(raw: str) -> str:
    """What a client id is: "mac", "ip" (IPv4 address) or "other" (CIDR, IPv6, ClientID)."""
    if norm_mac(raw):
        return "mac"
    try:
        return "ip" if isinstance(ipaddress.ip_address(raw), ipaddress.IPv4Address) else "other"
    except ValueError:
        return "other"


def _name(raw: Any) -> str | None:
    return str(raw or "").strip()[:63] or None


def parse_time(raw: Any) -> datetime | None:
    """AdGuard's RFC 3339 timestamps (with nanoseconds and a zone) as an aware datetime."""
    if not isinstance(raw, str) or not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    # Python's fromisoformat accepts at most 6 fractional digits.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else None


class AdGuardClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        https: bool = False,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        scheme = "https" if https else "http"
        self.base = f"{scheme}://{host.strip()}:{int(port)}"
        self._http = httpx.AsyncClient(
            base_url=self.base,
            auth=httpx.BasicAuth(username, password),
            timeout=timeout_s,
            follow_redirects=False,
            verify=False,  # noqa: S501 - LAN appliance, usually a self-signed certificate
            transport=transport,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = await self._http.get(path, params=params)
        except httpx.TimeoutException:
            raise AdGuardError("AdGuard Home did not answer in time") from None
        except httpx.HTTPError as exc:
            raise AdGuardError(f"cannot reach AdGuard Home ({type(exc).__name__})") from None
        if r.status_code in (401, 403):
            raise AdGuardLoginError("wrong username or password (or the account is locked out)")
        if r.status_code >= 300:
            raise AdGuardError(f"AdGuard Home answered HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise AdGuardError("unexpected answer (is this AdGuard Home?)") from None

    async def _post(self, path: str, body: dict) -> Any:
        try:
            r = await self._http.post(path, json=body)
        except httpx.TimeoutException:
            raise AdGuardError("AdGuard Home did not answer in time") from None
        except httpx.HTTPError as exc:
            raise AdGuardError(f"cannot reach AdGuard Home ({type(exc).__name__})") from None
        if r.status_code in (401, 403):
            raise AdGuardLoginError("wrong username or password (or the account is locked out)")
        if r.status_code >= 300:
            # AdGuard explains a refused change in plain text (e.g. an invalid upstream).
            reason = r.text.strip().splitlines()[0][:200] if r.text.strip() else ""
            raise AdGuardError(
                f"AdGuard Home refused the change (HTTP {r.status_code})"
                + (f": {reason}" if reason else "")
            )
        try:
            return r.json() if r.content.strip() else None
        except ValueError:
            return None  # "OK" and other plain answers

    # ── what NetWatch reads ──────────────────────────────────────────────────

    async def status(self) -> dict:
        s = await self._get("/control/status")
        if not isinstance(s, dict) or "protection_enabled" not in s:
            raise AdGuardError("unexpected answer (is this AdGuard Home?)")
        return {
            "version": str(s.get("version") or "")[:40] or None,
            "running": bool(s.get("running", True)),
            "protection": bool(s.get("protection_enabled")),
            "protection_resumes_ms": s.get("protection_disabled_duration") or None,
            "dhcp_available": bool(s.get("dhcp_available", False)),
            "dns_port": s.get("dns_port"),
        }

    async def stats(self) -> dict:
        s = await self._get("/control/stats")
        if not isinstance(s, dict):
            raise AdGuardError("unexpected statistics answer")
        return s

    async def querylog_enabled(self) -> bool:
        c = await self._get("/control/querylog/config")
        return bool(isinstance(c, dict) and c.get("enabled", True))

    async def querylog(
        self, newer_than: datetime | None, limit: int = 2000
    ) -> tuple[list[dict], bool]:
        """Entries newer than ``newer_than`` (newest first) and whether ``limit`` cut them short."""
        out: list[dict] = []
        older_than = ""
        while len(out) < limit:
            params = {"limit": min(MAX_PAGE, limit - len(out)), "response_status": "all"}
            if older_than:
                params["older_than"] = older_than
            page = await self._get("/control/querylog", params)
            data = page.get("data") if isinstance(page, dict) else None
            if not data:
                return out, False
            for e in data:
                ts = parse_time(e.get("time"))
                if ts is None:
                    continue
                if newer_than is not None and ts <= newer_than:
                    return out, False
                out.append({**e, "_ts": ts})
            older_than = str(page.get("oldest") or "")
            if not older_than:
                return out, False
        return out, True

    async def clients(self) -> list[dict]:
        """Known clients, one entry per id.

        Persistent clients (set up in AdGuard, possibly with several ids such as a MAC and an IP)
        share a ``group`` number; auto-detected ones (by IP only) have ``group`` None.
        """
        c = await self._get("/control/clients")
        if not isinstance(c, dict):
            return []
        out = []
        for g, cl in enumerate(c.get("clients") or []):
            for cid in cl.get("ids") or []:
                cid = str(cid).strip()[:64]
                kind = id_kind(cid)
                out.append({"id": norm_mac(cid) if kind == "mac" else cid, "kind": kind,
                            "name": _name(cl.get("name")), "group": g})  # fmt: skip
        for cl in c.get("auto_clients") or []:
            ip = str(cl.get("ip") or "").strip()[:64]
            if ip:
                out.append({"id": ip, "kind": id_kind(ip), "name": _name(cl.get("name")),
                            "group": None})  # fmt: skip
        return out

    async def dhcp_status(self) -> dict:
        """Whether AdGuard Home is the DHCP server, and its leases (dynamic and static)."""
        try:
            d = await self._get("/control/dhcp/status")
        except AdGuardLoginError:
            raise
        except AdGuardError:
            # Builds without DHCP support (and some installs) answer 404/501 here.
            return {"available": False, "enabled": False, "leases": []}
        if not isinstance(d, dict):
            return {"available": False, "enabled": False, "leases": []}
        leases = []
        for static, items in ((False, d.get("leases")), (True, d.get("static_leases"))):
            for item in items or []:
                mac = norm_mac(item.get("mac"))
                ip = str(item.get("ip") or "").strip()
                if not mac or id_kind(ip) != "ip":
                    continue
                leases.append({"mac": mac, "ip": ip, "hostname": _name(item.get("hostname")),
                               "static": static})  # fmt: skip
        v4 = d.get("v4") if isinstance(d.get("v4"), dict) else {}
        keys = ("gateway_ip", "subnet_mask", "range_start", "range_end")
        cfg = {k: str(v4.get(k) or "").strip() for k in keys}
        return {
            "available": True,
            "enabled": bool(d.get("enabled")),
            "leases": leases,
            # The DHCP settings, when AdGuard reports a complete, valid set.
            "v4": cfg if all(id_kind(v) == "ip" for v in cfg.values()) else None,
        }

    # ── what NetWatch can change (only through AdGuardActions) ───────────────

    async def set_protection(self, enabled: bool, duration_s: int | None = None) -> None:
        """Turn protection on, or off for ``duration_s`` (AdGuard resumes by itself)."""
        body: dict = {"enabled": enabled}
        if not enabled and duration_s:
            body["duration"] = int(duration_s) * 1000
        await self._post("/control/protection", body)

    async def user_rules(self) -> list[str]:
        f = await self._get("/control/filtering/status")
        # AdGuard Home sends "user_rules": null when there are no custom rules yet. A missing
        # key is refused: writing back a list we could not read would wipe the user's rules.
        if not isinstance(f, dict) or "user_rules" not in f:
            raise AdGuardError("unexpected filtering answer (is this AdGuard Home?)")
        rules = f["user_rules"]
        if rules is None:
            return []
        if not isinstance(rules, list):
            raise AdGuardError("unexpected filtering answer (is this AdGuard Home?)")
        return [str(r) for r in rules]

    async def persistent_clients(self) -> list[dict]:
        """AdGuard Home's persistent clients as it sends them (name, ids and settings)."""
        c = await self._get("/control/clients")
        items = c.get("clients") if isinstance(c, dict) else None
        if items is None:
            return []  # none set up yet (AdGuard sends null)
        if not isinstance(items, list):
            raise AdGuardError("unexpected clients answer (is this AdGuard Home?)")
        return [x for x in items if isinstance(x, dict) and x.get("name")]

    async def add_client(self, data: dict) -> None:
        await self._post("/control/clients/add", data)

    async def update_client(self, name: str, data: dict) -> None:
        await self._post("/control/clients/update", {"name": name, "data": data})

    async def delete_client(self, name: str) -> None:
        await self._post("/control/clients/delete", {"name": name})

    async def add_static_lease(self, mac: str, ip: str, hostname: str) -> None:
        await self._post("/control/dhcp/add_static_lease",
                         {"mac": mac, "ip": ip, "hostname": hostname})  # fmt: skip

    async def remove_static_lease(self, mac: str, ip: str, hostname: str) -> None:
        await self._post("/control/dhcp/remove_static_lease",
                         {"mac": mac, "ip": ip, "hostname": hostname})  # fmt: skip

    async def set_user_rules(self, rules: list[str]) -> None:
        await self._post("/control/filtering/set_rules", {"rules": rules})

    async def upstreams(self) -> dict:
        d = await self._get("/control/dns_info")
        if not isinstance(d, dict) or not isinstance(d.get("upstream_dns"), list):
            raise AdGuardError("unexpected DNS settings answer (is this AdGuard Home?)")
        return {
            "upstream_dns": [str(u) for u in d["upstream_dns"]],
            "bootstrap_dns": [str(u) for u in d.get("bootstrap_dns") or []],
        }

    async def test_upstreams(self, upstreams: list[str], bootstrap: list[str]) -> dict[str, str]:
        """AdGuard's own check of each upstream: "OK" or the error it got."""
        r = await self._post(
            "/control/test_upstream_dns", {"upstream_dns": upstreams, "bootstrap_dns": bootstrap}
        )
        r = r if isinstance(r, dict) else {}
        return {u: str(r.get(u, "no answer from the test"))[:200] for u in upstreams}

    async def set_upstreams(self, upstreams: list[str]) -> None:
        await self._post("/control/dns_config", {"upstream_dns": upstreams})
