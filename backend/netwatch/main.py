"""FastAPI application: API, health, optional /metrics, and the built React SPA."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import logging
import os
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .api import (
    admin,
    ask,
    devices,
    diagnosis,
    health,
    monitoring,
    netmap,
    score,
    system,
    targets,
    traffic,
    updates,
)
from .api import auth as auth_api
from .auth import COOKIE_NAME, CSRF_HEADER, CSRF_VALUE, AuthStore, LoginThrottle, issue_setup_token
from .config import Settings, get_settings
from .db import make_engine, run_migrations
from .probes import Prober
from .services.assistant import Assistant
from .services.bufferbloat import BufferbloatStore, BufferbloatTester
from .services.deco.service import DecoService
from .services.diagnosis import Diagnoser
from .services.discovery.inventory import DeviceStore
from .services.discovery.service import DiscoveryService
from .services.events import MONITOR, SECURITY, EventLog, IncidentRecorder, startup_events
from .services.exposure import ExposureScanner, ExposureStore
from .services.health import HealthService
from .services.hub.service import HubService, HubStore
from .services.metrics import Metrics
from .services.netinfo import default_route
from .services.netmap import NetMap
from .services.nic import NicMonitor, NicStore, interfaces_to_watch
from .services.pathtest import PATH_TEST_KINDS, PathStore, PathTester
from .services.report import build_report
from .services.restore import apply_pending_restore
from .services.runtime_settings import OVERRIDES_KEY, apply_values, load_overrides
from .services.scheduler import Monitor
from .services.secrets import SecretBox
from .services.seed import seed_defaults
from .services.storage import Store
from .services.throughput import ThroughputStore, ThroughputTester
from .services.traffic import HOUR_WATERMARK_KEY, TrafficStore
from .services.updates import UpdateManager

log = logging.getLogger(__name__)

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


# API paths reachable without a session (everything else under /api/ requires login).
_PUBLIC_API = frozenset({"/api/auth/status", "/api/auth/setup", "/api/auth/login"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# JSON bodies are small; only the backup upload may be large (it streams and checks its size).
MAX_BODY_BYTES = 256 * 1024
_LARGE_BODY = frozenset({"/api/admin/restore"})
_LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _host_name(header: str) -> str:
    """The host part of a Host header, without the port or IPv6 brackets."""
    h = header.strip().lower()
    if h.startswith("["):
        return h[1:].split("]", 1)[0]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_allowed(header: str | None, allowed: set[str]) -> bool:
    name = _host_name(header or "")
    if not name:
        return False
    if name in _LOCAL_NAMES or name in allowed:
        return True
    if allowed:
        return False
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


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
        restored_from = await asyncio.to_thread(
            apply_pending_restore, settings.db_path, settings.backup_dir
        )
        engine = make_engine(settings.db_path)
        await asyncio.to_thread(run_migrations, engine)
        store = Store(engine)
        # Values saved in the web UI override the environment.
        base = settings.model_copy()
        overrides = load_overrides(await asyncio.to_thread(store.kv_get, OVERRIDES_KEY, {}))
        apply_values(settings, overrides, base)
        logging.getLogger().setLevel(settings.log_level.upper())
        if settings.seed_defaults:
            await asyncio.to_thread(seed_defaults, store, default_route())
        metrics = Metrics() if settings.metrics_enabled else None

        events = EventLog(engine)
        await asyncio.to_thread(startup_events, events)
        if restored_from is not None:
            await asyncio.to_thread(
                events.instant,
                SECURITY,
                "warning",
                int(time.time() * 1000),
                "Database restored from a backup",
                details={"safety_copy": restored_from.name},
            )
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
        traffic_store = TrafficStore(engine)
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
                "traffic": rollup_and_purge_traffic(int(now)),
                "hub_stats": hub_store.purge(now_ms - settings.rollup_retention_days * day_ms),
                "bufferbloat_tests": bufferbloat_store.purge(
                    now_ms - settings.throughput_retention_days * day_ms
                ),
                "health_reports": app.state.health.purge(),
                "exposure_scans": exposure_store.purge(
                    now_ms - settings.throughput_retention_days * day_ms
                ),
            }

        def rollup_and_purge_traffic(now_s: int) -> int:
            minute_floor = now_s - settings.traffic_retention_days * 86400
            since = store.kv_get(HOUR_WATERMARK_KEY, None) or minute_floor
            traffic_store.rollup_hours(now_s, since_s=max(int(since), minute_floor))
            store.kv_set(HOUR_WATERMARK_KEY, now_s // 3600 * 3600)
            return traffic_store.purge(minute_floor, now_s - settings.rollup_retention_days * 86400)

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
        app.state.diagnoser = Diagnoser(
            store,
            events,
            path_store,
            gateway=default_route,
            raw_retention_days=settings.raw_retention_days,
            tz=settings.tz,
            devices=device_store,
        )
        app.state.discovery = discovery
        app.state.traffic = traffic_store
        bufferbloat_store = BufferbloatStore(engine)

        def quiet(until_ms: int) -> None:
            recorder.quiet_until_ms = max(recorder.quiet_until_ms, until_ms)

        app.state.bufferbloat = BufferbloatTester(bufferbloat_store, quiet=quiet)
        app.state.bufferbloat_store = bufferbloat_store
        hub_store = HubStore(engine)
        app.state.hub_store = hub_store
        app.state.hub = HubService(store, events, hub_store, SecretBox(settings.data_dir))
        app.state.deco = DecoService(
            store,
            events,
            device_store,
            discovery.inventory,
            SecretBox(settings.data_dir),
            traffic=traffic_store,
        )
        exposure_store = ExposureStore(engine)
        app.state.exposure_store = exposure_store
        app.state.exposure = ExposureScanner(exposure_store, engine, events, hub=app.state.hub)

        def health_report(start_ms: int, end_ms: int) -> dict:
            return build_report(
                app.state.diagnoser,
                throughput_store,
                start_ms,
                end_ms,
                hub_store=hub_store,
                bufferbloat_store=bufferbloat_store,
            )

        app.state.health = HealthService(
            engine, health_report, app.state.exposure.summary, tz=lambda: settings.tz
        )
        app.state.assistant = Assistant(
            store, SecretBox(settings.data_dir), app, tz=lambda: settings.tz
        )
        app.state.netmap = NetMap(app.state)
        app.state.auth = AuthStore(engine)
        app.state.setup_token = await asyncio.to_thread(
            issue_setup_token, app.state.auth, settings.data_dir
        )
        app.state.login_throttle = LoginThrottle(
            max_failures=settings.login_max_failures, lockout_s=settings.login_lockout_s
        )
        app.state.settings_base = base
        app.state.restart_pending = set()
        app.state.restart = _restart_process
        app.state.updates = UpdateManager(settings, store, client=update_client)
        await asyncio.to_thread(app.state.auth.purge_sessions)

        recorder.start()
        await monitor.start()
        nic.start()
        path_tester.start_schedule(path_test_targets, settings.path_test_interval_min)
        throughput.start_schedule()
        await discovery.start()
        app.state.deco.start()
        app.state.hub.start()
        app.state.bufferbloat.start_schedule(
            enabled=lambda: settings.bufferbloat_nightly,
            window=lambda: app.state.updates.policy().get("window", settings.auto_update_window),
            tz=lambda: settings.tz,
            kv_get=store.kv_get,
            kv_set=store.kv_set,
        )
        app.state.health.start_schedule()
        app.state.exposure.start_schedule(
            enabled=lambda: settings.exposure_scan_nightly,
            window=lambda: app.state.updates.policy().get("window", settings.auto_update_window),
            tz=lambda: settings.tz,
            kv_get=store.kv_get,
            kv_set=store.kv_set,
        )
        app.state.updates.start()
        log.info("netwatch started", extra={"version": __version__, "port": settings.port})
        try:
            yield
        finally:
            await app.state.updates.stop()
            await app.state.deco.stop()
            await app.state.hub.stop()
            await app.state.bufferbloat.stop()
            await app.state.exposure.stop()
            await app.state.health.stop()
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
        title="NetWatch",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        path = request.url.path
        is_api = path.startswith("/api/")
        if (is_api or path == "/metrics") and not host_allowed(
            request.headers.get("host"), settings.allowed_host_set
        ):
            # A page on another site that rebinds its name to this address gets nothing.
            name = _host_name(request.headers.get("host") or "")[:100] or "(none)"
            return JSONResponse(
                {
                    "detail": f"unknown host name {name!r}: add it to NETWATCH_ALLOWED_HOSTS "
                    "in .env and restart the container",
                    "host": name,
                },
                status_code=421,
            )
        if is_api and request.method not in _SAFE_METHODS and path not in _LARGE_BODY:
            length = request.headers.get("content-length")
            if length is None and request.headers.get("transfer-encoding"):
                return JSONResponse({"detail": "length required"}, status_code=411)
            if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
                return JSONResponse({"detail": "request body too large"}, status_code=413)
        if is_api and request.method not in _SAFE_METHODS:
            # CSRF defence in depth on top of SameSite=Strict: browsers won't send a custom
            # header cross-site without a CORS preflight, which this app never approves.
            if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        if (is_api and path not in _PUBLIC_API) or path == "/metrics":
            if path == "/metrics" and settings.metrics_token:
                given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                if hmac.compare_digest(given.encode(), settings.metrics_token.encode()):
                    return await call_next(request)
            token = request.cookies.get(COOKIE_NAME)
            user = (
                await asyncio.to_thread(
                    request.app.state.auth.resolve_session,
                    token,
                    settings.session_idle_hours * 3600,
                )
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
    app.include_router(diagnosis.router)
    app.include_router(admin.router)
    app.include_router(netmap.router)
    app.include_router(traffic.router)
    app.include_router(score.router)
    app.include_router(ask.router)

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint(request: Request) -> Response:
        # Registered always: whether it is enabled can be changed from the UI (after restart).
        m = getattr(request.app.state, "metrics", None)
        if m is None:
            raise HTTPException(404)
        return Response(generate_latest(m.registry), media_type=CONTENT_TYPE_LATEST)

    _mount_spa(app, settings.static_dir)
    return app


def _restart_process(delay_s: float = 1.0) -> None:
    """Stop gracefully after the response is sent; Docker's restart policy starts us again."""
    asyncio.get_running_loop().call_later(delay_s, os.kill, os.getpid(), signal.SIGTERM)


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
