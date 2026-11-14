"""Opt-in AdGuard Home actions (Admin → settings → "AdGuard Home actions").

Every change needs a signed-in session (and the X-Requested-With header, like every write), is
confirmed in the UI first, and is written to the security log, failures included.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..services.adguard.actions import ActionError, ActionsDisabled
from .auth import security_event

router = APIRouter(prefix="/api/adguard/actions")


class PauseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(ge=1, le=1440)


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=1, max_length=253)
    kind: Literal["allow", "block"]


class RuleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str = Field(min_length=1, max_length=300)


class UpstreamsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upstreams: list[str] = Field(min_length=1, max_length=5)


def _acts(request: Request):
    return request.app.state.adguard_actions


def _who(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return getattr(user, "username", None) or "admin"


def _clock(request: Request, ms: int) -> str:
    try:
        tz = ZoneInfo(request.app.state.settings.tz)
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        tz = None
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%H:%M")


async def _run(request: Request, what: str, coro):
    """Run an action; log success or failure to the security log; map errors to HTTP."""
    try:
        result = await coro
    except ActionsDisabled as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ActionError as exc:
        await security_event(request, "warning", f"AdGuard Home: could not {what}: {exc}",
                             {"action": "adguard", "ok": False})  # fmt: skip
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    return result


@router.get("")
async def actions_state(request: Request) -> dict:
    acts = _acts(request)
    state = await asyncio.to_thread(acts.state)
    state["upstreams"] = None
    if state["enabled"]:
        # Not set up or unreachable: the page says so from the status.
        with contextlib.suppress(ActionError, ActionsDisabled):
            state["upstreams"] = await acts.current_upstreams()
    return state


@router.post("/pause")
async def pause(body: PauseIn, request: Request) -> dict:
    p = await _run(request, "pause protection", _acts(request).pause(body.minutes, _who(request)))
    await security_event(
        request, "warning",
        f"AdGuard Home: protection paused for {body.minutes} min (until "
        f"{_clock(request, p['until_ms'])})",
        {"action": "adguard_pause", "minutes": body.minutes},
    )  # fmt: skip
    return {"pause": p}


@router.post("/resume")
async def resume(request: Request) -> dict:
    await _run(request, "resume protection", _acts(request).resume(_who(request)))
    await security_event(request, "info", "AdGuard Home: protection turned back on",
                         {"action": "adguard_resume"})  # fmt: skip
    return {"pause": None}


@router.post("/rules")
async def add_rule(body: RuleIn, request: Request) -> dict:
    entry = await _run(
        request, f"{body.kind} {body.domain[:80]}",
        _acts(request).add_rule(body.domain, body.kind, _who(request)),
    )  # fmt: skip
    verb = "allowed" if body.kind == "allow" else "blocked"
    await security_event(request, "info", f"AdGuard Home: {verb} {entry['domain']}",
                         {"action": "adguard_rule", "rule": entry["rule"]})  # fmt: skip
    return {"rule": entry, "rules": _acts(request).rules()}


@router.delete("/rules")
async def remove_rule(body: RuleOut, request: Request) -> dict:
    entry = await _run(request, "remove a rule", _acts(request).remove_rule(body.rule))
    await security_event(request, "info", f"AdGuard Home: removed rule {entry['rule']}",
                         {"action": "adguard_rule_removed", "rule": entry["rule"]})  # fmt: skip
    return {"rules": _acts(request).rules()}


@router.post("/upstreams/test")
async def test_upstreams(body: UpstreamsIn, request: Request) -> dict:
    try:
        return await _acts(request).test_upstreams(body.upstreams)
    except ActionsDisabled as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ActionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


@router.post("/upstreams")
async def switch_upstreams(body: UpstreamsIn, request: Request) -> dict:
    r = await _run(request, "switch the upstream DNS",
                   _acts(request).switch_upstreams(body.upstreams))  # fmt: skip
    await _log_switch(request, r)
    return r


@router.post("/upstreams/restore")
async def restore_upstreams(request: Request) -> dict:
    r = await _run(request, "switch back to the previous upstream DNS",
                   _acts(request).restore_upstreams())  # fmt: skip
    await _log_switch(request, r)
    return r


async def _log_switch(request: Request, r: dict) -> None:
    await security_event(
        request, "warning",
        f"AdGuard Home: upstream DNS switched from {', '.join(r['previous'])} to "
        f"{', '.join(r['current'])}",
        {"action": "adguard_upstreams", "previous": r["previous"], "current": r["current"]},
    )  # fmt: skip
