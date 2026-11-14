"""Read-only client for the AdGuard Home REST API (``/control/*``, HTTP basic auth).

NetWatch only ever sends GET requests: status, statistics, the query log and the client list.
Every failure becomes an ``AdGuardError`` whose message is safe to show (it never contains the
password or the Authorization header).
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
        return {"available": True, "enabled": bool(d.get("enabled")), "leases": leases}
