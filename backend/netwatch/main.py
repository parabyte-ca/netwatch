"""FastAPI application: API, health, optional /metrics, and the built React SPA."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .api import auth as auth_api
from .api import devices, health, monitoring, system, targets, updates
from .auth import COOKIE_NAME, CSRF_HEADER, CSRF_VALUE, AuthStore, LoginThrottle
from .config import Settings, get_settings
from .db import make_engine, run_migrations
from .probes import Prober
from .services.discovery.inventory import DeviceStore
from .services.discovery.service import DiscoveryService
from .services.events import MONITOR, EventLog, IncidentRecorder, startup_events
from .services.metrics import Metrics
from .services.netinfo import default_route
from .services.nic import NicMonitor, NicStore, interfaces_to_watch
from .services.pathtest import PATH_TEST_KINDS, PathStore, PathTester
from .services.scheduler import Monitor
from .services.seed import seed_defaults
from .services.storage import Store
from .services.throughput import ThroughputStore, ThroughputTester
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

        events = EventLog(engine)
        await asyncio.to_thread(startup_events, events)
        path_store = PathStore(engine)
        path_tester = PathTester(
            path_store, cycles=settings.path_test_cycles, cooldown_s=settings.path_test_cooldown_s
        )

        async def on_outage(spec, event_id: int) -> None:
            if settings.path_test_on_outage:
                path_tester.trigger_for_outage(spec, event_id)

        recorder = IncidentRecorder(events, on_outage=on_outage)
        monitor = Monitor(store, settings, prober=prober, metrics=metrics, recorder=recorder)

        def watched_interfaces() -> list[str]:
            route = default_route()
            return interfaces_to_watch(
                settings.sysfs_root, settings.nic_interface_list, route.interface if route else None
            )

        nic_store = NicStore(engine)
        nic = NicMonitor(
            events,
            nic_store,
            sys_root=settings.sysfs_root,
            interfaces=watched_interfaces,
            poll_s=settings.nic_poll_s,
        )
        throughput_store = ThroughputStore(engine)
        throughput = ThroughputTester(throughput_store)
        device_store = DeviceStore(engine)
        discovery = DiscoveryService(
            settings, store, events, device_store, on_targets_changed=monitor.reload
        )
        path_tester.on_result.append(discovery.anomalies.check_path)

        def purge_extra(now: float) -> dict:
            now_ms, day_ms = int(now * 1000), 86_400_000
            return {
                "events": events.purge(now_ms - settings.event_retention_days * day_ms),
                "path_tests": path_store.purge(now_ms - settings.path_test_retention_days * day_ms),
                "throughput_tests": throughput_store.purge(
                    now_ms - settings.throughput_retention_days * day_ms
                ),
                "nic_minutes": nic_store.purge(int(now) - settings.rollup_retention_days * 86400),
                "devices": device_store.purge(now_ms - settings.device_retention_days * day_ms),
                "device_ips": device_store.purge_ips(
                    now_ms - settings.device_retention_days * day_ms
                ),
            }

        monitor.purge_hooks.append(purge_extra)

        def path_test_targets() -> list[dict]:
            return [
                t
                for t in store.list_targets(enabled_only=True)
                if t["group"] == "public" and t["kind"] in PATH_TEST_KINDS
            ]

        app.state.settings = settings
        app.state.store = store
        app.state.monitor = monitor
        app.state.metrics = metrics
        app.state.events = events
        app.state.recorder = recorder
        app.state.nic = nic
        app.state.nic_store = nic_store
        app.state.path_store = path_store
        app.state.path_tester = path_tester
        app.state.throughput_store = throughput_store
        app.state.throughput = throughput
        app.state.device_store = device_store
        app.state.discovery = discovery
        app.state.auth = AuthStore(engine)
        app.state.login_throttle = LoginThrottle()
        app.state.updates = UpdateManager(settings, store, client=update_client)
        await asyncio.to_thread(app.state.auth.purge_sessions)

        recorder.start()
        await monitor.start()
        nic.start()
        path_tester.start_schedule(path_test_targets, settings.path_test_interval_min)
        throughput.start_schedule()
        await discovery.start()
        app.state.updates.start()
        log.info("netwatch started", extra={"version": __version__, "port": settings.port})
        try:
            yield
        finally:
            await app.state.updates.stop()
            await discovery.stop()
            await throughput.stop()
            await path_tester.stop()
            await nic.stop()
            await monitor.stop()
            await recorder.stop()
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    events.instant, MONITOR, "info", int(time.time() * 1000), "NetWatch stopped"
                )
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
    app.include_router(monitoring.router)
    app.include_router(devices.router)

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
