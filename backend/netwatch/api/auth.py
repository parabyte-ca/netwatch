from __future__ import annotations

import asyncio
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from ..auth import COOKIE_NAME, AuthStore, LoginThrottle, password_problem
from ..services.events import SECURITY

router = APIRouter(prefix="/api/auth")

_USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = v.strip()
        if not _USERNAME.match(v):
            raise ValueError("letters, digits and . _ @ - only")
        return v


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def _store(request: Request) -> AuthStore:
    return request.app.state.auth


def _set_cookie(request: Request, response: Response, token: str) -> None:
    s = request.app.state.settings
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=s.session_days * 86400,
        httponly=True,
        samesite="strict",
        secure=s.cookie_secure,
        path="/",
    )


async def security_event(
    request: Request, severity: str, title: str, details: dict | None = None
) -> None:
    """Record a sign-in / admin action on the timeline (kind "security")."""
    events = getattr(request.app.state, "events", None)
    if events is None:
        return
    await asyncio.to_thread(
        events.instant,
        SECURITY,
        severity,
        int(time.time() * 1000),
        title,
        details={"client": _client(request), **(details or {})},
    )


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _new_session(request: Request, response: Response, user_id: int) -> None:
    s = request.app.state.settings
    token = await asyncio.to_thread(
        _store(request).create_session,
        user_id,
        s.session_days,
        _client(request),
        request.headers.get("user-agent"),
    )
    _set_cookie(request, response, token)


@router.get("/status")
async def auth_status(request: Request) -> dict:
    store = _store(request)
    users = await asyncio.to_thread(store.user_count)
    user = None
    token = request.cookies.get(COOKIE_NAME)
    if token and users:
        user = await asyncio.to_thread(store.resolve_session, token)
    return {
        "setup_required": users == 0,
        "authenticated": user is not None,
        "username": user.username if user else None,
    }


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(body: Credentials, request: Request, response: Response) -> dict:
    """First-run only: create the admin account and sign in."""
    if problem := password_problem(body.password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    uid = await asyncio.to_thread(_store(request).create_first_user, body.username, body.password)
    if uid is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "setup has already been completed")
    await _new_session(request, response, uid)
    return {"username": body.username}


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response) -> dict:
    throttle: LoginThrottle = request.app.state.login_throttle
    key = _client(request)
    if wait := throttle.retry_after(key):
        await security_event(request, "warning", "Sign-in refused: client locked out")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"too many failed attempts; try again in {wait} s",
            headers={"Retry-After": str(wait)},
        )
    user = await asyncio.to_thread(_store(request).authenticate, body.username, body.password)
    if user is None:
        throttle.failure(key)
        locked = throttle.retry_after(key) > 0
        await security_event(
            request,
            "warning",
            "Failed sign-in; client locked out" if locked else "Failed sign-in",
            {"username": body.username},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
    throttle.success(key)
    await security_event(request, "info", f"{user.username} signed in")
    await _new_session(request, response, user.user_id)
    return {"username": user.username}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await asyncio.to_thread(_store(request).delete_session, token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.post("/password")
async def change_password(body: PasswordChange, request: Request, response: Response) -> dict:
    """Change the signed-in user's password. Signs out every other session."""
    user = request.state.user
    store = _store(request)
    if await asyncio.to_thread(store.authenticate, user.username, body.current_password) is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "current password is incorrect")
    if problem := password_problem(body.new_password):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, problem)
    await asyncio.to_thread(store.set_password, user.user_id, body.new_password)
    await security_event(request, "info", f"{user.username} changed their password")
    await _new_session(request, response, user.user_id)
    return {"username": user.username}
