"""Path tests with mtr: hop-by-hop loss and latency, scheduled and triggered by outages.

Reading mtr correctly matters: loss at an intermediate hop that does not continue to the
destination is usually just that router rate-limiting ICMP replies, not real loss. The useful
evidence is the *first hop from which loss persists all the way to the end*; that is where the
problem starts, and it is recorded as ``first_loss_hop``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

from sqlalchemy import Engine, delete, insert, select

from ..db import path_tests
from ..probes.base import TargetSpec

log = logging.getLogger(__name__)

PATH_TEST_KINDS = ("icmp", "tcp", "http")
RUN_TIMEOUT_S = 120


def parse_mtr_json(text: str) -> list[dict]:
    """Normalise ``mtr --json`` output into a hop list (versions differ in key types)."""
    data = json.loads(text)
    hubs = data.get("report", {}).get("hubs", [])
    hops = []
    for i, h in enumerate(hubs, start=1):
        host = str(h.get("host", "???"))

        def f(key: str, h=h) -> float | None:
            v = h.get(key)
            return round(float(v), 3) if isinstance(v, int | float) else None

        try:
            count = int(h.get("count", i))
        except (TypeError, ValueError):
            count = i
        hops.append(
            {
                "hop": count,
                "host": None if host == "???" else host,
                "loss_pct": f("Loss%") or 0.0,
                "sent": int(h.get("Snt") or 0),
                "last": f("Last"),
                "avg": f("Avg"),
                "best": f("Best"),
                "worst": f("Wrst"),
                "stdev": f("StDev"),
            }
        )
    return hops


def first_persistent_loss(hops: list[dict]) -> int | None:
    """Hop number from which every later responding hop (including the last) shows loss.

    Hops that never answer (``???``) are skipped: many routers simply don't reply to probes.
    """
    answering = [h for h in hops if h["host"]]
    if not answering or answering[-1]["loss_pct"] <= 0:
        return None
    start = None
    for h in reversed(answering):
        if h["loss_pct"] > 0:
            start = h["hop"]
        else:
            break
    return start


def summarise(hops: list[dict]) -> dict:
    # mtr stops at the destination, so a last hop that answered means it was reached.
    last = hops[-1] if hops else None
    reached = bool(last and last["host"] and last["loss_pct"] < 100)
    return {
        "hop_count": len(hops),
        "reached": reached,
        "first_loss_hop": first_persistent_loss(hops),
    }


Runner = Callable[[list[str], float], Awaitable[tuple[int, str, str]]]


async def run_process(cmd: list[str], limit_s: float) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), limit_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return 124, "", "timed out"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class PathStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert(self, row: dict) -> dict:
        with self.engine.begin() as conn:
            rid = conn.execute(insert(path_tests).values(**row)).inserted_primary_key[0]
        return self.get(rid)

    def get(self, test_id: int) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(path_tests).where(path_tests.c.id == test_id)).first()
        return self._row(r) if r else None

    def previous_ok(self, host: str) -> dict | None:
        stmt = (
            select(path_tests)
            .where(path_tests.c.host == host, path_tests.c.status == "ok")
            .order_by(path_tests.c.ts_ms.desc())
            .limit(1)
        )
        with self.engine.connect() as conn:
            r = conn.execute(stmt).first()
        return self._row(r) if r else None

    def list(
        self,
        *,
        target_id: int | None = None,
        event_id: int | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        stmt = select(path_tests)
        if target_id is not None:
            stmt = stmt.where(path_tests.c.target_id == target_id)
        if event_id is not None:
            stmt = stmt.where(path_tests.c.event_id == event_id)
        if start_ms is not None:
            stmt = stmt.where(path_tests.c.ts_ms >= start_ms)
        if end_ms is not None:
            stmt = stmt.where(path_tests.c.ts_ms < end_ms)
        stmt = stmt.order_by(path_tests.c.ts_ms.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [self._row(r) for r in conn.execute(stmt)]

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(path_tests).where(path_tests.c.ts_ms < cutoff_ms)).rowcount

    @staticmethod
    def _row(r) -> dict:
        d = dict(r._mapping)
        d["hops"] = json.loads(d["hops"] or "[]")
        return d


class PathTester:
    def __init__(
        self,
        store: PathStore,
        *,
        cycles: int = 10,
        cooldown_s: float = 300,
        runner: Runner = run_process,
        max_parallel: int = 2,
    ) -> None:
        self.store = store
        self.cycles = cycles
        self.cooldown_s = cooldown_s
        self.runner = runner
        self._sem = asyncio.Semaphore(max_parallel)
        self._last_auto: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._loop_task is None or not self._loop_task.done()

    async def run(
        self,
        host: str,
        *,
        trigger: str,
        target_id: int | None = None,
        event_id: int | None = None,
    ) -> dict:
        """Run mtr against host (a validated hostname/IP; never starts with '-') and store it."""
        cmd = [
            "mtr", "--json", "--no-dns", "--report-cycles", str(self.cycles),
            "--interval", "0.2", "--max-ttl", "30", host,
        ]  # fmt: skip
        started = time.time()
        async with self._sem:
            try:
                rc, out, err = await self.runner(cmd, RUN_TIMEOUT_S)
            except FileNotFoundError:
                rc, out, err = 127, "", "mtr is not installed"
        row = {
            "ts_ms": int(started * 1000),
            "target_id": target_id,
            "event_id": event_id,
            "host": host,
            "trigger": trigger,
            "duration_ms": int((time.time() - started) * 1000),
            "hops": "[]",
        }
        if rc != 0:
            row.update(status="error", error=(err.strip() or f"mtr exited {rc}")[:200])
        else:
            try:
                hops = parse_mtr_json(out)
            except (ValueError, AttributeError) as exc:
                row.update(status="error", error=f"unparseable mtr output: {exc}"[:200])
            else:
                prev = await asyncio.to_thread(self.store.previous_ok, host)
                seq = [h["host"] for h in hops if h["host"]]
                changed = None
                if prev is not None:
                    changed = seq != [h["host"] for h in prev["hops"] if h["host"]]
                row.update(
                    status="ok",
                    hops=json.dumps(hops),
                    path_changed=changed,
                    **summarise(hops),
                )
        return await asyncio.to_thread(self.store.insert, row)

    def trigger_for_outage(self, spec: TargetSpec, event_id: int) -> None:
        """Fire-and-forget path test when a target goes down (rate-limited per host)."""
        if spec.kind not in PATH_TEST_KINDS:
            return
        now = time.monotonic()
        if now - self._last_auto.get(spec.host, -1e9) < self.cooldown_s:
            return
        self._last_auto[spec.host] = now
        task = asyncio.create_task(
            self._safe_run(spec.host, trigger="outage", target_id=spec.id, event_id=event_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_run(self, host: str, **kw) -> None:
        try:
            await self.run(host, **kw)
        except Exception:
            log.exception("path test failed", extra={"host": host})

    def start_schedule(self, targets: Callable[[], list[dict]], interval_min: int) -> None:
        if interval_min <= 0:
            return

        async def loop() -> None:
            await asyncio.sleep(60)
            while True:
                rows = await asyncio.to_thread(targets)
                for t in rows:
                    await self._safe_run(t["host"], trigger="scheduled", target_id=t["id"])
                await asyncio.sleep(interval_min * 60)

        self._loop_task = asyncio.create_task(loop(), name="netwatch-pathtests")

    async def stop(self) -> None:
        tasks = [*self._tasks, *([self._loop_task] if self._loop_task else [])]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        self._loop_task = None
