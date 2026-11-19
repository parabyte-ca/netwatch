"""Saved servers for the SSH app link: a name, an address on the LAN, a port and a user.

No login is stored or used here; the link is opened by an SSH app on the phone or computer.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.servers import ServerError
from .admin import _lan_host
from .auth import security_event

router = APIRouter(prefix="/api/admin/servers")

_USER = r"^[a-z_][a-z0-9_-]{0,31}$"


class ServerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(pattern=_USER)
    notes: str | None = Field(default=None, max_length=200)


class ServerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=40)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, pattern=_USER)
    notes: str | None = Field(default=None, max_length=200)


def _store(request: Request):
    return request.app.state.servers


async def _run(fn, *args, **kw):
    try:
        return await asyncio.to_thread(fn, *args, **kw)
    except ServerError as exc:
        code = 404 if str(exc) == "no such server" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(code, str(exc)) from None


def _where(s: dict) -> str:
    return f"{s['username']}@{s['host']}" + (f":{s['port']}" if s["port"] != 22 else "")


@router.get("")
async def list_servers(request: Request) -> dict:
    return {"servers": await asyncio.to_thread(_store(request).list)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_server(body: ServerIn, request: Request) -> dict:
    host = await asyncio.to_thread(_lan_host, body.host, "the server")
    s = await _run(_store(request).create, body.name, host, body.port, body.username, body.notes)
    await security_event(
        request, "info", f"Servers: added {s['name']} ({_where(s)})",
        {"action": "server_added", "server_id": s["id"]},
    )  # fmt: skip
    return s


@router.put("/{server_id}")
async def edit_server(server_id: int, body: ServerPatch, request: Request) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("host") is not None:
        changes["host"] = await asyncio.to_thread(_lan_host, changes["host"], "the server")
    s = await _run(_store(request).update, server_id, **changes)
    await security_event(
        request, "info", f"Servers: changed {s['name']} ({_where(s)})",
        {"action": "server_changed", "server_id": server_id},
    )  # fmt: skip
    return s


@router.delete("/{server_id}")
async def delete_server(server_id: int, request: Request) -> dict:
    s = await _run(_store(request).delete, server_id)
    await security_event(
        request, "info", f"Servers: removed {s['name']}",
        {"action": "server_removed", "server_id": server_id},
    )  # fmt: skip
    return {"removed": s["name"]}


@router.get("/suggestions")
async def suggestions(request: Request) -> dict:
    """Addresses NetWatch already knows, to fill the form: the integrations and online devices."""
    st = request.app.state
    saved = {s["host"] for s in await asyncio.to_thread(st.servers.list)}
    out: list[dict] = []
    seen: set[str] = set(saved)

    def add(host: str | None, label: str) -> None:
        h = (host or "").strip().lower()
        if h and h not in seen and len(out) < 40:
            seen.add(h)
            out.append({"host": h, "label": label})

    add(st.adguard.config().get("host"), "AdGuard Home")
    add(st.deco.config().get("host"), "Main Deco")
    add(st.hub.config().get("host"), "Home Hub")
    now = int(time.time() * 1000)
    rows = await asyncio.to_thread(st.discovery.inventory.listing, now)
    for d in rows:
        if d.get("online") and d.get("last_ip"):
            add(d["last_ip"], d["label"])
    return {"suggestions": out}
