from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends

from .. import __version__
from ..config import Settings
from ..services.netinfo import default_route
from ..services.scheduler import Monitor
from .deps import get_app_settings, get_monitor

router = APIRouter(prefix="/api")


@router.get("/system")
async def system(
    monitor: Monitor = Depends(get_monitor), settings: Settings = Depends(get_app_settings)
) -> dict:
    route = default_route()
    db_bytes = await asyncio.to_thread(monitor.store.size_bytes)
    return {
        "version": __version__,
        "time": time.time(),
        "started_at": monitor.started_at,
        "default_route": {"interface": route.interface, "gateway": route.gateway}
        if route
        else None,
        "database_bytes": db_bytes,
        "active_targets": monitor.active_targets,
        "pending_samples": monitor.pending_samples,
        "last_flush_at": monitor.last_flush_at,
        "last_rollup_at": monitor.last_rollup_at,
        "retention": {
            "raw_days": settings.raw_retention_days,
            "rollup_days": settings.rollup_retention_days,
        },
        "metrics_enabled": settings.metrics_enabled,
    }
