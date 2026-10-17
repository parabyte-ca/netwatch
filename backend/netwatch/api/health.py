from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import __version__

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    store = request.app.state.store
    monitor = request.app.state.monitor
    try:
        db_ok = await asyncio.to_thread(store.ping)
    except Exception:
        db_ok = False
    checks = {
        "database": db_ok,
        "scheduler": monitor.running,
        "writer": monitor.last_flush_error is None,
    }
    healthy = all(checks.values())
    body = {
        "status": "ok" if healthy else "unhealthy",
        "version": __version__,
        "uptime_s": round(time.time() - (monitor.started_at or time.time()), 1),
        "checks": checks,
    }
    if monitor.last_flush_error:
        body["writer_error"] = monitor.last_flush_error
    return JSONResponse(body, status_code=200 if healthy else 503)
