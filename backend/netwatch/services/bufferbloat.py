"""Bufferbloat (latency under load) test.

Measures latency to a public anchor (1.1.1.1, beyond Bell's network) while the line is idle,
then while it is saturated downloading, then uploading. When the bottleneck (usually the Home
Hub's WAN side) has oversized buffers, packets queue behind the bulk transfer and latency
jumps: that is what makes video calls stutter while someone downloads a game. Graded like
Waveform's test, on the worse of the two loaded phases.

Load comes from Cloudflare's speed-test endpoints (several parallel streams), so no iperf3
server is needed. A test moves a few GB on gigabit fibre and takes about 30 seconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import statistics
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from icmplib import async_ping
from sqlalchemy import Engine, delete, insert, select

from ..db import bufferbloat_tests

log = logging.getLogger(__name__)

ANCHOR = "1.1.1.1"
DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
UP_URL = "https://speed.cloudflare.com/__up"
IDLE_S, LOAD_S, PING_EVERY_S = 5.0, 10.0, 0.1
STREAMS = 6
CHUNK = 1 << 20
MIN_LOAD_BYTES = 2_000_000  # below this the line was not loaded: no grade
LAST_RUN_KEY = "bufferbloat_last_scheduled"

# (grade, maximum added latency in ms) — Waveform's scale
GRADES = (("A+", 5), ("A", 30), ("B", 60), ("C", 200), ("D", 400))

Pinger = Callable[[str], Awaitable[float | None]]
Loader = Callable[[str, float], Awaitable[int]]  # direction, seconds → bytes moved


def grade(added_ms: float | None) -> str | None:
    if added_ms is None:
        return None
    for g, limit in GRADES:
        if added_ms <= limit:
            return g
    return "F"


async def icmp_ping(host: str) -> float | None:
    try:
        r = await async_ping(host, count=1, timeout=1.0, privileged=True)
    except Exception:
        return None
    return r.rtts[0] if r.is_alive and r.rtts else None


class HttpLoad:
    """Saturate the line with parallel HTTPS streams for a fixed time."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client

    async def __call__(self, direction: str, seconds: float) -> int:
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=15.0))
        moved = [0]
        deadline = time.monotonic() + seconds

        async def down() -> None:
            while time.monotonic() < deadline:
                async with client.stream("GET", DOWN_URL.format(n=250_000_000)) as r:
                    async for chunk in r.aiter_bytes(CHUNK):
                        moved[0] += len(chunk)
                        if time.monotonic() >= deadline:
                            return

        async def up() -> None:
            block = os.urandom(CHUNK)

            async def body():
                while time.monotonic() < deadline:
                    moved[0] += len(block)
                    yield block

            while time.monotonic() < deadline:
                await client.post(UP_URL, content=body())

        worker = down if direction == "download" else up
        tasks = [asyncio.create_task(worker()) for _ in range(STREAMS)]
        results: list = []
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=seconds + 10
            )
        except TimeoutError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            if own:
                await client.aclose()
        errors = [r for r in results if isinstance(r, Exception)]
        if errors and moved[0] < MIN_LOAD_BYTES:
            raise errors[0]
        return moved[0]


class BufferbloatStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def add(self, row: dict) -> dict:
        values = {**row, "details": json.dumps(row.get("details") or {})}
        with self.engine.begin() as conn:
            rid = conn.execute(insert(bufferbloat_tests).values(**values)).inserted_primary_key[0]
        return {**row, "id": rid}

    def list(self, limit: int = 60) -> list[dict]:
        stmt = select(bufferbloat_tests).order_by(bufferbloat_tests.c.ts_ms.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [
                {**dict(r._mapping), "details": json.loads(r.details or "{}")}
                for r in conn.execute(stmt)
            ]

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(bufferbloat_tests).where(bufferbloat_tests.c.ts_ms < cutoff_ms)
            ).rowcount


def _stats(samples: list[float | None]) -> dict:
    ok = sorted(s for s in samples if s is not None)
    if not ok:
        return {"median": None, "p95": None, "loss_pct": 100.0 if samples else None, "n": 0}
    return {
        "median": round(statistics.median(ok), 2),
        "p95": round(ok[min(len(ok) - 1, int(0.95 * len(ok)))], 2),
        "loss_pct": round(100 * (len(samples) - len(ok)) / len(samples), 1),
        "n": len(samples),
    }


class BufferbloatTester:
    def __init__(
        self,
        store: BufferbloatStore,
        *,
        pinger: Pinger = icmp_ping,
        loader: Loader | None = None,
        quiet: Callable[[int], None] | None = None,
        idle_s: float = IDLE_S,
        load_s: float = LOAD_S,
    ) -> None:
        self.store = store
        self.pinger = pinger
        self.loader = loader or HttpLoad()
        self.quiet = quiet  # called with "until" ms: silence packet-loss alerts meanwhile
        self.idle_s, self.load_s = idle_s, load_s
        self.running = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._manual: asyncio.Task | None = None

    async def _sample(self, seconds: float) -> list[float | None]:
        out: list[float | None] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            t0 = time.monotonic()
            out.append(await self.pinger(ANCHOR))
            await asyncio.sleep(max(0.0, PING_EVERY_S - (time.monotonic() - t0)))
        return out

    async def _loaded(self, direction: str) -> tuple[list[float | None], float]:
        started = time.monotonic()
        load = asyncio.create_task(self.loader(direction, self.load_s))
        await asyncio.sleep(1.0)  # let TCP ramp up before measuring
        samples = await self._sample(self.load_s - 1.0)
        moved = await load
        if moved < MIN_LOAD_BYTES:
            # Grading unloaded latency would hand out a false A+.
            raise RuntimeError(f"could not load the line for the {direction} test")
        return samples, moved * 8 / max(time.monotonic() - started, 0.001)

    async def run(self, trigger: str = "manual") -> dict:
        if self._lock.locked():
            raise RuntimeError("a bufferbloat test is already running")
        async with self._lock:
            self.running = True
            ts = int(time.time() * 1000)
            if self.quiet:
                self.quiet(ts + int((self.idle_s + 2 * self.load_s + 30) * 1000))
            try:
                idle = _stats(await self._sample(self.idle_s))
                down_s, down_bps = await self._loaded("download")
                up_s, up_bps = await self._loaded("upload")
            except Exception as exc:
                log.warning("bufferbloat test failed", extra={"error": str(exc)})
                return await asyncio.to_thread(
                    self.store.add,
                    {
                        "ts_ms": ts,
                        "trigger": trigger,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}"[:200],
                        "details": {},
                    },
                )
            finally:
                self.running = False
            down, up = _stats(down_s), _stats(up_s)
            if idle["median"] is None:
                row = {
                    "ts_ms": ts,
                    "trigger": trigger,
                    "status": "error",
                    "error": f"{ANCHOR} did not answer pings",
                    "details": {"idle": idle},
                }
                return await asyncio.to_thread(self.store.add, row)
            added = [p["median"] - idle["median"] for p in (down, up) if p["median"] is not None]
            worst = max(added) if added else None
            row = {
                "ts_ms": ts,
                "trigger": trigger,
                "status": "ok",
                "grade": grade(worst) if worst is not None else "F",
                "idle_ms": idle["median"],
                "down_ms": down["median"],
                "up_ms": up["median"],
                "down_bps": round(down_bps),
                "up_bps": round(up_bps),
                "details": {
                    "anchor": ANCHOR,
                    "idle": idle,
                    "download": down,
                    "upload": up,
                    "added_ms": round(worst, 1) if worst is not None else None,
                },
            }
            log.info("bufferbloat test", extra={"grade": row["grade"], "added_ms": worst})
            return await asyncio.to_thread(self.store.add, row)

    # ── nightly schedule ─────────────────────────────────────────────────────

    def start_schedule(
        self,
        *,
        enabled: Callable[[], bool],
        window: Callable[[], str],
        tz: Callable[[], str],
        kv_get: Callable,
        kv_set: Callable,
    ) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    if not enabled():
                        continue
                    zone = ZoneInfo(tz())
                    now = datetime.now(zone)
                    h, m = (int(x) for x in (await asyncio.to_thread(window)).split(":"))
                    # 20 minutes after the update window opens, so an update has finished.
                    start = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(
                        minutes=20
                    )
                    today = now.date().isoformat()
                    last = await asyncio.to_thread(kv_get, LAST_RUN_KEY, None)
                    if start <= now < start + timedelta(hours=2) and last != today:
                        await asyncio.to_thread(kv_set, LAST_RUN_KEY, today)
                        await self.run("scheduled")
                except Exception:
                    log.exception("scheduled bufferbloat test failed")

        self._task = asyncio.create_task(loop(), name="netwatch-bufferbloat")

    def start_now(self) -> bool:
        """Run in the background (the API returns at once; the page polls for the result)."""
        if self._lock.locked():
            return False
        self._manual = asyncio.create_task(self.run("manual"), name="netwatch-bufferbloat-run")
        return True

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
