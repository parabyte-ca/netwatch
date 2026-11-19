"""How to reach a device: an SSH link and its admin web page.

Nothing here logs in. NetWatch only knows what the exposure scan found (port 22 with an OpenSSH
banner, web ports) and the user name and port you save, and hands out ``ssh://`` and ``http(s)://``
links that your own SSH app or browser opens.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from sqlalchemy import Engine, delete, insert, select, update

from ..db import host_access
from .exposure import describe_port

_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MODES = ("auto", "on", "off")
SSH_PORT = 22
# Web ports in the order a person would try them: the usual ones first.
WEB_PORTS = {
    443: "https",
    80: "http",
    8443: "https",
    8080: "http",
    5000: "http",
    8123: "http",
    32400: "http",
}
CHECK_PORTS = (SSH_PORT, *WEB_PORTS)


class AccessError(ValueError):
    pass


def ssh_url(host: str, port: int, username: str) -> str:
    """``ssh://user@host`` (IPv6 in brackets; the port only when it is not 22)."""
    h = f"[{host}]" if ":" in host else host
    return f"ssh://{username}@{h}" + (f":{port}" if port != SSH_PORT else "")


def ssh_command(host: str, port: int, username: str) -> str:
    return f"ssh {username}@{host}" + (f" -p {port}" if port != SSH_PORT else "")


def web_url(scheme: str, host: str, port: int) -> str:
    default = {"http": 80, "https": 443}[scheme]
    h = f"[{host}]" if ":" in host else host
    return f"{scheme}://{h}" + (f":{port}" if port != default else "")


class AccessStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, *, device_id: int | None = None, target_id: int | None = None) -> dict | None:
        col = host_access.c.device_id if device_id is not None else host_access.c.target_id
        with self.engine.connect() as conn:
            row = conn.execute(
                select(host_access).where(
                    col == (device_id if device_id is not None else target_id)
                )
            ).first()
        return dict(row._mapping) if row else None

    @staticmethod
    def _clean(values: dict, hosts: frozenset[str]) -> dict:
        out: dict = {}
        if "ssh_user" in values:
            user = (values["ssh_user"] or "").strip()
            if user and not _USER.match(user):
                raise AccessError("a user name of lower-case letters, digits, _ and - is needed")
            out["ssh_user"] = user or None
        if "ssh_port" in values:
            port = values["ssh_port"]
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise AccessError("the port must be 1 to 65535")
            out["ssh_port"] = port
        if "ssh_mode" in values:
            if values["ssh_mode"] not in MODES:
                raise AccessError("the SSH setting must be auto, on or off")
            out["ssh_mode"] = values["ssh_mode"]
        if "admin_url" in values:
            url = (values["admin_url"] or "").strip()
            if url:
                parts = urlsplit(url)
                if parts.scheme not in ("http", "https") or not parts.hostname or len(url) > 200:
                    raise AccessError("the admin page must be an http or https address")
                if parts.hostname.lower() not in hosts:
                    raise AccessError("the admin page must be on this device's own address")
            out["admin_url"] = url or None
        return out

    def save(
        self,
        *,
        device_id: int | None = None,
        target_id: int | None = None,
        hosts: frozenset[str] = frozenset(),
        **changes,
    ) -> dict:
        if (device_id is None) == (target_id is None):
            raise AccessError("one of device or target is needed")
        v = self._clean({k: x for k, x in changes.items() if k in _EDITABLE}, hosts)
        owner = {"device_id": device_id} if device_id is not None else {"target_id": target_id}
        with self.engine.begin() as conn:
            col = host_access.c.device_id if device_id is not None else host_access.c.target_id
            existing = conn.execute(
                select(host_access.c.id).where(col == (device_id or target_id))
            ).first()
            if existing is None:
                conn.execute(insert(host_access).values(**owner, **v))
            elif v:
                conn.execute(update(host_access).where(host_access.c.id == existing[0]).values(**v))
        return self.get(device_id=device_id, target_id=target_id)  # type: ignore[return-value]

    def forget(self, *, device_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(host_access).where(host_access.c.device_id == device_id))


_EDITABLE = {"ssh_user", "ssh_port", "ssh_mode", "admin_url"}


def hosts_of(ip: str | None, hostname: str | None) -> frozenset[str]:
    """The names an admin page may live on: the device's own address and host name."""
    out = {h.lower() for h in (ip, hostname) if h}
    return frozenset(out)


def view(row: dict | None, services: list[dict], ip: str | None, *, scanned: bool = True) -> dict:
    """What the profile shows: the SSH link and the web pages, from the scan and the saved row.

    ``scanned`` False is a remote server, which is never scanned: SSH then shows unless hidden,
    and the only admin page is the address you saved."""
    row = row or {}
    mode = row.get("ssh_mode") or "auto"
    user = row.get("ssh_user")
    port = row.get("ssh_port") or SSH_PORT
    ssh_seen = next((s for s in services if s["port"] == SSH_PORT), None)
    if scanned:
        available = mode == "on" or (mode == "auto" and ssh_seen is not None)
    else:
        available = mode != "off"
    ssh = {
        "available": available and bool(ip),
        "mode": mode,
        "detected": ssh_seen is not None,
        "banner": ssh_seen.get("banner") if ssh_seen else None,
        "user": user,
        "port": port,
        "ssh_url": ssh_url(ip, port, user) if ip and user else None,
        "ssh_command": ssh_command(ip, port, user) if ip and user else None,
    }
    open_ports = {s["port"] for s in services}
    pages = []
    for p, scheme in WEB_PORTS.items():
        if p in open_ports and ip:
            pages.append(
                {
                    "port": p,
                    "scheme": scheme,
                    "url": web_url(scheme, ip, p),
                    "label": describe_port(p)["service"],
                }
            )
    override = row.get("admin_url")
    if override:
        primary = {"port": None, "scheme": urlsplit(override).scheme, "url": override,
                   "label": "Saved address"}  # fmt: skip
        others = pages
    else:
        primary, others = (pages[0], pages[1:]) if pages else (None, [])
    seen = [s["last_seen_ms"] for s in services if s.get("last_seen_ms")]
    return {
        "ip": ip,
        "scanned": scanned,
        "ssh": ssh,
        "web": {"primary": primary, "others": others},
        "admin_url": override,
        "scanned_ms": max(seen) if seen else None,
    }
