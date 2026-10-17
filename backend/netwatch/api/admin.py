"""Admin panel: settings, sessions, security log, database backup and restore, restart."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .. import __version__
from ..auth import COOKIE_NAME, token_hash
from ..services.detector import DetectorConfig
from ..services.events import SECURITY
from ..services.restore import (
    PENDING_NAME,
    RestoreError,
    backup_path,
    list_backups,
    stage_restore,
    unpack_upload,
)
from ..services.runtime_settings import (
    BY_NAME,
    OVERRIDES_KEY,
    SettingsError,
    apply_values,
    describe,
    merge_overrides,
    validate_changes,
)
from ..services.storage import backup_database
from .auth import security_event

router = APIRouter(prefix="/api/admin")
log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 4 * 1024**3
SECRET_MASK = "••••••••"  # noqa: S105 - placeholder shown instead of the metrics token


# ── settings ──────────────────────────────────────────────────────────────────


def apply_live(state) -> None:
    """Push values the running services hold on to (the rest read Settings each time)."""
    s = state.settings
    state.monitor.detector.config = DetectorConfig(
        down_after=s.down_after,
        up_after=s.up_after,
        degraded_enter_pct=s.degraded_enter_pct,
        degraded_exit_pct=s.degraded_exit_pct,
    )
    state.path_tester.cycles = s.path_test_cycles
    state.path_tester.cooldown_s = s.path_test_cooldown_s
    state.nic.poll_s = s.nic_poll_s
    state.discovery.anomalies.storm_pps = s.arp_storm_pps
    state.login_throttle.max_failures = s.login_max_failures
    state.login_throttle.lockout_s = s.login_lockout_s
    logging.getLogger().setLevel(s.log_level.upper())


async def _settings_body(request: Request) -> dict:
    st = request.app.state
    overrides = await asyncio.to_thread(st.store.kv_get, OVERRIDES_KEY, {})
    return {
        "fields": describe(st.settings, st.settings_base, overrides or {}),
        "restart_pending": sorted(st.restart_pending),
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    return await _settings_body(request)


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, object]


@router.put("/settings")
async def put_settings(body: SettingsIn, request: Request) -> dict:
    st = request.app.state
    changes = {k: v for k, v in body.changes.items() if not (BY_NAME.get(k) and v == SECRET_MASK)}
    try:
        values = validate_changes(st.settings, changes, st.settings_base)
    except SettingsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    changed = {
        k
        for k, v in values.items()
        if (getattr(st.settings_base, k) if v is None else v) != getattr(st.settings, k)
    }
    current = await asyncio.to_thread(st.store.kv_get, OVERRIDES_KEY, {}) or {}
    await asyncio.to_thread(st.store.kv_set, OVERRIDES_KEY, merge_overrides(current, values))
    apply_values(st.settings, values, st.settings_base)
    apply_live(st)
    st.restart_pending |= {k for k in changed if not BY_NAME[k].live}
    if changed:
        await security_event(
            request,
            "info",
            "Settings changed: " + ", ".join(BY_NAME[k].label for k in sorted(changed)),
            {"settings": sorted(changed)},
        )
    return await _settings_body(request)


@router.post("/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart(request: Request) -> dict:
    """Restart NetWatch (Docker's restart policy brings it back within seconds)."""
    await security_event(request, "info", "NetWatch restart requested")
    request.app.state.restart()
    return {"restarting": True}


# ── sessions ──────────────────────────────────────────────────────────────────


@router.get("/sessions")
async def sessions(request: Request) -> list[dict]:
    current = token_hash(request.cookies.get(COOKIE_NAME, ""))
    rows = await asyncio.to_thread(request.app.state.auth.list_sessions)
    return [
        {
            "id": r["token_hash"],
            "username": r["username"],
            "created_ms": r["created_ms"],
            "last_seen_ms": r["last_seen_ms"],
            "expires_ms": r["expires_ms"],
            "ip": r["ip"],
            "user_agent": r["user_agent"],
            "current": r["token_hash"] == current,
        }
        for r in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(session_id: str, request: Request) -> Response:
    if session_id == token_hash(request.cookies.get(COOKIE_NAME, "")):
        raise HTTPException(status.HTTP_409_CONFLICT, "use Sign out to end your own session")
    if not await asyncio.to_thread(request.app.state.auth.delete_session_hash, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    await security_event(request, "info", "A session was signed out by an administrator")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/revoke-others")
async def revoke_others(request: Request) -> dict:
    n = await asyncio.to_thread(
        request.app.state.auth.delete_other_sessions, request.cookies.get(COOKIE_NAME, "")
    )
    await security_event(request, "info", f"Signed out {n} other session(s)")
    return {"revoked": n}


@router.get("/security-log")
async def security_log(request: Request, days: int = 30) -> list[dict]:
    now = int(time.time() * 1000)
    days = max(1, min(days, 365))
    return await asyncio.to_thread(
        request.app.state.events.list, now - days * 86_400_000, now + 1, kinds=[SECURITY],
        limit=500,
    )  # fmt: skip


# ── backups and restore ───────────────────────────────────────────────────────


@router.get("/backups")
async def backups(request: Request) -> dict:
    s = request.app.state.settings
    items = await asyncio.to_thread(list_backups, s.backup_dir)
    return {
        "backups": items,
        "restore_pending": (s.data_dir / PENDING_NAME).exists(),
        "database_bytes": await asyncio.to_thread(request.app.state.store.size_bytes),
    }


@router.post("/backups", status_code=status.HTTP_201_CREATED)
async def create_backup(request: Request) -> dict:
    s = request.app.state.settings
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = s.backup_dir / f"netwatch-{stamp}-v{__version__}.db"
    path = await asyncio.to_thread(backup_database, s.db_path, dest)
    await security_event(request, "info", f"Backup created: {path.name}")
    return {"name": path.name, "bytes": path.stat().st_size, "modified": path.stat().st_mtime}


@router.get("/backups/{name}")
async def download_backup(name: str, request: Request) -> FileResponse:
    try:
        p = backup_path(request.app.state.settings.backup_dir, name)
    except (RestoreError, FileNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found") from exc
    return FileResponse(p, media_type="application/vnd.sqlite3", filename=p.name)


@router.delete("/backups/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(name: str, request: Request) -> Response:
    try:
        p = backup_path(request.app.state.settings.backup_dir, name)
    except (RestoreError, FileNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found") from exc
    await asyncio.to_thread(p.unlink)
    await security_event(request, "info", f"Backup deleted: {name}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _stage_and_restart(request: Request, candidate, source: str) -> dict:
    s = request.app.state.settings
    try:
        info = await asyncio.to_thread(stage_restore, s.data_dir, candidate)
    except RestoreError as exc:
        candidate.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    await security_event(request, "warning", f"Database restore started from {source}", info)
    request.app.state.restart()
    return {"restarting": True, **info}


@router.post("/backups/{name}/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_existing(name: str, request: Request) -> dict:
    s = request.app.state.settings
    try:
        src = backup_path(s.backup_dir, name)
    except (RestoreError, FileNotFoundError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backup not found") from exc
    candidate = s.data_dir / "restore-candidate.db"
    await asyncio.to_thread(candidate.write_bytes, src.read_bytes())
    return await _stage_and_restart(request, candidate, name)


@router.post("/restore", status_code=status.HTTP_202_ACCEPTED)
async def restore_upload(request: Request) -> dict:
    """Upload a backup (.db, or .db.gz) as the raw request body; applied after a restart."""
    s = request.app.state.settings
    upload = s.data_dir / "restore-upload.tmp"
    size = 0
    try:
        with upload.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")
                f.write(chunk)
        if size == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty upload")
        candidate = s.data_dir / "restore-candidate.db"
        await asyncio.to_thread(unpack_upload, upload, candidate)
    except HTTPException:
        upload.unlink(missing_ok=True)
        raise
    except OSError as exc:
        upload.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"bad upload: {exc}") from exc
    return await _stage_and_restart(request, candidate, "an uploaded file")
