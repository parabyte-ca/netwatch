"""Saved servers: a short list of machines (AdGuard Home, TrueNAS, ...) to open an SSH app on.

NetWatch holds no login for them: each entry is a name, an address, a port and a user name,
and the link it gives out (``ssh://user@host:port``) is opened by an SSH app on the phone or
computer. Addresses are checked to be on the LAN by the API before they reach this store.
"""

from __future__ import annotations

import re
import time

from sqlalchemy import Engine, delete, func, insert, select, update

from ..db import servers

MAX_SERVERS = 50
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_FIELDS = {"name", "host", "port", "username", "notes"}


class ServerError(ValueError):
    pass


def ssh_url(host: str, port: int, username: str) -> str:
    """``ssh://user@host`` (IPv6 in brackets; the port only when it is not 22)."""
    h = f"[{host}]" if ":" in host else host
    return f"ssh://{username}@{h}" + (f":{port}" if port != 22 else "")


def ssh_command(host: str, port: int, username: str) -> str:
    return f"ssh {username}@{host}" + (f" -p {port}" if port != 22 else "")


def _view(row) -> dict:
    d = dict(row._mapping)
    d["ssh_url"] = ssh_url(d["host"], d["port"], d["username"])
    d["ssh_command"] = ssh_command(d["host"], d["port"], d["username"])
    return d


class ServerStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(servers).order_by(servers.c.sort, servers.c.name)).all()
        return [_view(r) for r in rows]

    def get(self, server_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(servers).where(servers.c.id == server_id)).first()
        return _view(row) if row else None

    @staticmethod
    def _clean(values: dict) -> dict:
        out: dict = {}
        if "name" in values:
            name = " ".join(str(values["name"]).split())
            if not name or len(name) > 40:
                raise ServerError("a name of 1 to 40 characters is needed")
            out["name"] = name
        if "host" in values:
            host = str(values["host"]).strip().lower()
            if not host or len(host) > 253 or any(c.isspace() for c in host):
                raise ServerError("an address is needed")
            out["host"] = host
        if "port" in values:
            port = values["port"]
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise ServerError("the port must be 1 to 65535")
            out["port"] = port
        if "username" in values:
            user = str(values["username"]).strip()
            if not _USER.match(user):
                raise ServerError("a user name of lower-case letters, digits, _ and - is needed")
            out["username"] = user
        if "notes" in values:
            notes = " ".join(str(values["notes"] or "").split())
            if len(notes) > 200:
                raise ServerError("notes can be up to 200 characters")
            out["notes"] = notes or None
        return out

    def _unique(self, conn, name: str, exclude: int | None) -> None:
        q = select(servers.c.id).where(func.lower(servers.c.name) == name.lower())
        if exclude is not None:
            q = q.where(servers.c.id != exclude)
        if conn.execute(q).first():
            raise ServerError("a server with that name already exists")

    def create(
        self, name: str, host: str, port: int, username: str, notes: str | None = None
    ) -> dict:
        v = self._clean(
            {"name": name, "host": host, "port": port, "username": username, "notes": notes}
        )
        with self.engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(servers)).scalar_one() >= MAX_SERVERS:
                raise ServerError(f"at most {MAX_SERVERS} servers can be saved")
            self._unique(conn, v["name"], None)
            top = conn.execute(select(func.max(servers.c.sort))).scalar()
            new_id = conn.execute(
                insert(servers).values(**v, sort=(top or 0) + 1, created_ms=int(time.time() * 1000))
            ).inserted_primary_key[0]
        return self.get(new_id)  # type: ignore[return-value]

    def update(self, server_id: int, **changes) -> dict:
        v = self._clean({k: x for k, x in changes.items() if k in _FIELDS})
        with self.engine.begin() as conn:
            if conn.execute(select(servers.c.id).where(servers.c.id == server_id)).first() is None:
                raise ServerError("no such server")
            if "name" in v:
                self._unique(conn, v["name"], server_id)
            if v:
                conn.execute(update(servers).where(servers.c.id == server_id).values(**v))
        return self.get(server_id)  # type: ignore[return-value]

    def delete(self, server_id: int) -> dict:
        row = self.get(server_id)
        if row is None:
            raise ServerError("no such server")
        with self.engine.begin() as conn:
            conn.execute(delete(servers).where(servers.c.id == server_id))
        return row
