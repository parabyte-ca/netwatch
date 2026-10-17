"""Async probe scheduler, batched sample writer, and periodic maintenance (rollups, retention)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time

from ..config import Settings
from ..probes import Prober, ProbeResult, TargetSpec
from .live import LiveState
from .metrics import Metrics
from .storage import Store

log = logging.getLogger(__name__)

_MAX_BUFFER = 50_000  # drop oldest beyond this if the DB is unavailable for a long time


class Monitor:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        *,
        prober: Prober | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.prober = prober or Prober(icmp_privileged=settings.icmp_privileged)
        self.metrics = metrics
        self.live = LiveState()
        self._sem = asyncio.Semaphore(settings.max_concurrent_probes)
        self._specs: dict[int, TargetSpec] = {}
        self._tasks: dict[int, asyncio.Task] = {}
        self._buffer: list[ProbeResult] = []
        self._background: list[asyncio.Task] = []
        self._reload_lock = asyncio.Lock()
        self.started_at: float | None = None
        self.last_flush_at: float | None = None
        self.last_flush_error: str | None = None
        self.last_rollup_at: float | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.started_at is not None and all(not t.done() for t in self._background)

    async def start(self) -> None:
        self.started_at = time.time()
        await self.reload()
        self._background = [
            asyncio.create_task(self._flush_loop(), name="netwatch-flush"),
            asyncio.create_task(self._maintenance_loop(), name="netwatch-maintenance"),
        ]
        log.info("monitor started", extra={"targets": len(self._specs)})

    async def stop(self) -> None:
        tasks = [*self._tasks.values(), *self._background]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        self._background.clear()
        await self.flush()
        await self.prober.aclose()
        self.started_at = None
        log.info("monitor stopped")

    async def reload(self) -> None:
        """Bring running probe tasks in line with the enabled targets in the database."""
        async with self._reload_lock:
            rows = await asyncio.to_thread(self.store.list_targets, enabled_only=True)
            wanted = {r["id"]: TargetSpec.from_row(r) for r in rows}
            for tid in list(self._tasks):
                if tid not in wanted or wanted[tid] != self._specs.get(tid):
                    self._tasks.pop(tid).cancel()
                    self._specs.pop(tid, None)
                    if tid not in wanted:
                        self.live.forget(tid)
                        if self.metrics:
                            self.metrics.forget(tid)
            for tid, spec in wanted.items():
                if tid in self._tasks:
                    continue
                self._specs[tid] = spec
                self.live.track(tid, spec.interval_s)
                if self.metrics:
                    self.metrics.track(spec)
                self._tasks[tid] = asyncio.create_task(self._run_target(spec), name=f"probe-{tid}")

    # ── probing ──────────────────────────────────────────────────────────────

    def _record(self, r: ProbeResult) -> None:
        self.live.record(r)
        if self.metrics:
            self.metrics.record(r)
        self._buffer.append(r)
        if len(self._buffer) > _MAX_BUFFER:
            del self._buffer[: len(self._buffer) - _MAX_BUFFER]

    async def run_once(self, spec: TargetSpec) -> ProbeResult:
        async with self._sem:
            return await self.prober.run(spec)

    async def _run_target(self, spec: TargetSpec) -> None:
        loop = asyncio.get_running_loop()
        # Spread targets across the interval so probes don't fire in lock-step.
        await asyncio.sleep(random.uniform(0, min(spec.interval_s, 5.0)))
        next_at = loop.time()
        while True:
            async with self._sem:
                result = await self.prober.run(spec)
            self._record(result)
            next_at += spec.interval_s
            delay = next_at - loop.time()
            if delay < 0:  # overran (slow probe or event-loop stall): skip missed ticks
                next_at = loop.time()
                delay = 0
            await asyncio.sleep(delay)

    # ── persistence ──────────────────────────────────────────────────────────

    async def flush(self) -> int:
        if not self._buffer:
            return 0
        batch, self._buffer = self._buffer, []
        try:
            n = await asyncio.to_thread(self.store.insert_samples, batch)
        except Exception as exc:
            self._buffer[:0] = batch  # retry on next flush
            self.last_flush_error = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("sample flush failed", extra={"pending": len(self._buffer)})
            return 0
        self.last_flush_at = time.time()
        self.last_flush_error = None
        return n

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.flush_interval_s)
            await self.flush()

    async def maintenance(self, now: float | None = None, *, purge: bool = False) -> None:
        now = time.time() if now is None else now
        s = self.settings
        n = await asyncio.to_thread(self.store.rollup_pending, now, s.raw_retention_days)
        self.last_rollup_at = now
        if n:
            log.debug("rollups written", extra={"buckets": n})
        if purge:
            removed = await asyncio.to_thread(
                self.store.purge, now, s.raw_retention_days, s.rollup_retention_days
            )
            log.info("retention purge", extra=removed)

    async def _maintenance_loop(self) -> None:
        last_purge = 0.0
        last_optimize = time.time()
        while True:
            # Run 20 s past each minute so the previous minute is complete and flushed.
            now = time.time()
            await asyncio.sleep(60 - (now % 60) + 20)
            now = time.time()
            try:
                do_purge = now - last_purge >= 3600
                await self.maintenance(now, purge=do_purge)
                if do_purge:
                    last_purge = now
                if now - last_optimize >= 86400:
                    await asyncio.to_thread(self.store.optimize)
                    last_optimize = now
            except Exception:
                log.exception("maintenance failed")

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def active_targets(self) -> int:
        return len(self._tasks)

    @property
    def pending_samples(self) -> int:
        return len(self._buffer)
