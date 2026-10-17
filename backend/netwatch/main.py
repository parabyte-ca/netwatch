"""FastAPI application: API, health, optional /metrics, and the built React SPA."""

from __future__ import annotations

import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .api import auth as auth_api
from .api import health, system, targets, updates
from .auth import COOKIE_NAME, CSRF_HEADER, CSRF_VALUE, AuthStore, LoginThrottle
from .config import Settings, get_settings
from .db import make_engine, run_migrations
from .probes import Prober
from .services.metrics import Metrics
from .services.netinfo import default_route
from .services.scheduler import Monitor
from .services.seed import seed_defaults
from .services.storage import Store
from .services.updates import UpdateManager

log = logging.getLogger(__name__)

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


# API paths reachable without a session (everything else under /api/ requires login).
_PUBLIC_API = frozenset({"/api/auth/status", "/api/auth/setup", "/api/auth/login"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_app(
    settings: Settings | None = None,
    *,
    prober: Prober | None = None,
    update_client: httpx.AsyncClient | None = None,
) -> FastAPI:
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
        app.state.auth = AuthStore(engine)
        app.state.login_throttle = LoginThrottle()
        app.state.updates = UpdateManager(settings, store, client=update_client)
        await asyncio.to_thread(app.state.auth.purge_sessions)
        await monitor.start()
        app.state.updates.start()
        log.info("netwatch started", extra={"version": __version__, "port": settings.port})
        try:
            yield
        finally:
            await app.state.updates.stop()
            await monitor.stop()
            engine.dispose()

    app = FastAPI(
        title="NetWatch", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        path = request.url.path
        is_api = path.startswith("/api/")
        if is_api and request.method not in _SAFE_METHODS:
            # CSRF defence in depth on top of SameSite=Strict: browsers won't send a custom
            # header cross-site without a CORS preflight, which this app never approves.
            if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        if (is_api and path not in _PUBLIC_API) or (
            path == "/metrics" and settings.metrics_enabled
        ):
            if path == "/metrics" and settings.metrics_token:
                given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                if hmac.compare_digest(given, settings.metrics_token):
                    return await call_next(request)
            token = request.cookies.get(COOKIE_NAME)
            user = (
                await asyncio.to_thread(request.app.state.auth.resolve_session, token)
                if token
                else None
            )
            if user is None:
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            request.state.user = user
        return await call_next(request)

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
    app.include_router(auth_api.router)
    app.include_router(system.router)
    app.include_router(targets.router)
    app.include_router(updates.router)

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
