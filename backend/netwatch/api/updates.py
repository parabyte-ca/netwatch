from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..services.updates import UpdateError, UpdateManager

router = APIRouter(prefix="/api/updates")


class ApplyBody(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class PolicyBody(BaseModel):
    mode: str
    window: str


def _mgr(request: Request) -> UpdateManager:
    return request.app.state.updates


def _fail(exc: UpdateError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.get("")
async def summary(request: Request) -> dict:
    return await asyncio.to_thread(_mgr(request).summary)


@router.post("/check")
async def check_now(request: Request) -> dict:
    try:
        await _mgr(request).check(manual=True)
    except UpdateError as exc:
        raise _fail(exc) from exc
    return await asyncio.to_thread(_mgr(request).summary)


@router.post("/apply", status_code=status.HTTP_202_ACCEPTED)
async def apply(body: ApplyBody, request: Request) -> dict:
    """Ask the host agent to install a release. Progress is reported via GET /api/updates."""
    try:
        return await asyncio.to_thread(
            _mgr(request).request_update,
            body.version,
            reason="manual",
            by=request.state.user.username,
        )
    except UpdateError as exc:
        raise _fail(exc) from exc


@router.post("/rollback", status_code=status.HTTP_202_ACCEPTED)
async def rollback(request: Request) -> dict:
    """Undo the last update (code, image and database, via update.sh --rollback)."""
    try:
        return await asyncio.to_thread(
            _mgr(request).request_rollback, by=request.state.user.username
        )
    except UpdateError as exc:
        raise _fail(exc) from exc


@router.put("/policy")
async def set_policy(body: PolicyBody, request: Request) -> dict:
    try:
        return await asyncio.to_thread(_mgr(request).set_policy, body.mode, body.window)
    except UpdateError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/log", response_class=PlainTextResponse)
async def upgrade_log(request: Request) -> str:
    mgr = _mgr(request)
    return await asyncio.to_thread(mgr.folder.log_tail) if mgr.folder else ""
