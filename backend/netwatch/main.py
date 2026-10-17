"""FastAPI application: API, health, optional /metrics, and the built React SPA."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .api import health, system, targets
from .config import Settings, get_settings
from .db import make_engine, run_migrations
from .probes import Prober
from .services.metrics import Metrics
from .services.netinfo import default_route
from .services.scheduler import Monitor
from .services.seed import seed_defaults
from .services.storage import Store

log = logging.getLogger(__name__)

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def create_app(settings: Settings | None = None, *, prober: Prober | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = make_engine(settings.db_path)
        await asyncio.to_thread(run_migrations, engine)
        store = Store(engine)
        if settings.seed_defaults:
            await asyncio.to_thread(seed_defaults, store, default_route())
        metrics = Metrics() if settings.metrics_enabled else None
        monitor = Monitor(store, settings, prober=prober, metrics=metrics)
        app.state.settings = settings
        app.state.store = store
        app.state.monitor = monitor
        app.state.metrics = metrics
        await monitor.start()
        log.info("netwatch started", extra={"version": __version__, "port": settings.port})
        try:
            yield
        finally:
            await monitor.stop()
            engine.dispose()

    app = FastAPI(
        title="NetWatch", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", _CSP)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(targets.router)

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        def metrics_endpoint(request: Request) -> Response:
            registry = request.app.state.metrics.registry
            return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    _mount_spa(app, settings.static_dir)
    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    if not index.is_file():
        log.warning(
            "frontend build not found; serving API only", extra={"static_dir": str(static_dir)}
        )
        return
    root = static_dir.resolve()

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            cache = (
                "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            )
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
