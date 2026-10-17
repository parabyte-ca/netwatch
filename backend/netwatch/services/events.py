"""Event timeline: storage plus the recorder that turns detector transitions into events."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable

from sqlalchemy import Engine, and_, delete, insert, or_, select, update

from ..db import events
from ..probes.base import TargetSpec, now_ms
from .detector import DEGRADED, DOWN, Transition

log = logging.getLogger(__name__)

# Event kinds shown on the timeline.
OUTAGE, LOSS, NIC_LINK, NIC_SPEED, NIC_ERRORS, MONITOR, ANOMALY, DEVICE, SECURITY = (
    "outage",
    "degraded",
    "nic_link",
    "nic_speed",
    "nic_errors",
    "monitor",
    "anomaly",
    "device",
    "security",
)
KINDS = (OUTAGE, LOSS, NIC_LINK, NIC_SPEED, NIC_ERRORS, MONITOR, ANOMALY, DEVICE, SECURITY)


def _row(r) -> dict:
    d = dict(r._mapping)
    d["details"] = json.loads(d["details"] or "{}")
    d["duration_ms"] = (d["end_ms"] - d["start_ms"]) if d["end_ms"] is not None else None
    d["open"] = d["end_ms"] is None
    return d


class EventLog:
    """Synchronous event storage (call from worker threads)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def open(
        self,
        kind: str,
        severity: str,
        start_ms: int,
        title: str,
        *,
        target_id: int | None = None,
        iface: str | None = None,
        details: dict | None = None,
        end_ms: int | None = None,
    ) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                insert(events).values(
                    kind=kind,
                    severity=severity,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    title=title[:200],
                    target_id=target_id,
                    iface=iface,
                    details=json.dumps(details or {}),
                )
            ).inserted_primary_key[0]

    def instant(self, kind: str, severity: str, ts_ms: int, title: str, **kw) -> int:
        """A point-in-time event (start == end)."""
        return self.open(kind, severity, ts_ms, title, end_ms=ts_ms, **kw)

    def close(self, event_id: int, end_ms: int, extra: dict | None = None) -> None:
        with self.engine.begin() as conn:
            row = conn.execute(select(events.c.details).where(events.c.id == event_id)).first()
            if row is None:
                return
            details = {**json.loads(row[0] or "{}"), **(extra or {})}
            conn.execute(
                update(events)
                .where(events.c.id == event_id, events.c.end_ms.is_(None))
                .values(end_ms=end_ms, details=json.dumps(details))
            )

    def close_interrupted(self, end_ms: int) -> int:
        """Close events left open by an unclean stop; they can no longer be observed ending."""
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(events.c.id, events.c.details).where(events.c.end_ms.is_(None))
            )
            n = 0
            for eid, raw in rows.all():
                details = {**json.loads(raw or "{}"), "interrupted": True}
                conn.execute(
                    update(events)
                    .where(events.c.id == eid)
                    .values(end_ms=end_ms, details=json.dumps(details))
                )
                n += 1
        return n

    def get(self, event_id: int) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(events).where(events.c.id == event_id)).first()
        return _row(r) if r else None

    def list(
        self,
        start_ms: int,
        end_ms: int,
        *,
        kinds: Iterable[str] | None = None,
        target_id: int | None = None,
        open_only: bool = False,
        limit: int = 500,
    ) -> list[dict]:
        """Events overlapping [start_ms, end_ms), newest first."""
        stmt = select(events).where(
            events.c.start_ms < end_ms,
            or_(events.c.end_ms.is_(None), events.c.end_ms >= start_ms),
        )
        kinds = list(kinds or [])
        if kinds:
            stmt = stmt.where(events.c.kind.in_(kinds))
        if target_id is not None:
            stmt = stmt.where(events.c.target_id == target_id)
        if open_only:
            stmt = stmt.where(events.c.end_ms.is_(None))
        stmt = stmt.order_by(events.c.start_ms.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(stmt)]

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(events).where(
                    and_(events.c.end_ms.is_not(None), events.c.end_ms < cutoff_ms)
                )
            ).rowcount


OutageHook = Callable[[TargetSpec, int], Awaitable[None]]


class IncidentRecorder:
    """Serialises detector transitions into open/close event writes on one worker task."""

    def __init__(self, log_store: EventLog, *, on_outage: OutageHook | None = None) -> None:
        self.events = log_store
        self.on_outage = on_outage
        self._queue: asyncio.Queue = asyncio.Queue()
        self._open: dict[tuple[int, str], int] = {}
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        self._task = asyncio.create_task(self._worker(), name="netwatch-incidents")

    async def stop(self) -> None:
        await self.drain()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def drain(self) -> None:
        if self.running:
            await self._queue.join()

    def submit(self, t: Transition, spec: TargetSpec) -> None:
        self._queue.put_nowait(("transition", t, spec))

    def target_removed(self, target_id: int, ts_ms: int) -> None:
        self._queue.put_nowait(("removed", target_id, ts_ms))

    def open_event_id(self, target_id: int, kind: str = OUTAGE) -> int | None:
        return self._open.get((target_id, kind))

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item[0] == "transition":
                    await self._handle(item[1], item[2])
                else:
                    await self._close_all(item[1], item[2], {"monitoring_stopped": True})
            except Exception:
                log.exception("failed to record incident")
            finally:
                self._queue.task_done()

    async def _close(self, target_id: int, kind: str, end_ms: int, extra: dict) -> None:
        eid = self._open.pop((target_id, kind), None)
        if eid is not None:
            await asyncio.to_thread(self.events.close, eid, end_ms, extra)

    async def _close_all(self, target_id: int, end_ms: int, extra: dict) -> None:
        for kind in (OUTAGE, LOSS):
            await self._close(target_id, kind, end_ms, extra)

    async def _handle(self, t: Transition, spec: TargetSpec) -> None:
        if t.prev == DOWN:
            await self._close(t.target_id, OUTAGE, t.ts_ms, {"recovered_to": t.new})
        if t.prev == DEGRADED:
            await self._close(t.target_id, LOSS, t.ts_ms, {"ended_as": t.new})

        base = {
            "target_name": spec.name,
            "host": spec.host,
            "probe": spec.kind,
            "group": spec.group,
        }
        if t.new == DOWN:
            eid = await asyncio.to_thread(
                self.events.open,
                OUTAGE,
                "critical",
                t.ts_ms,
                f"{spec.name} unreachable",
                target_id=t.target_id,
                details={**base, "last_error": t.last_error},
            )
            self._open[(t.target_id, OUTAGE)] = eid
            if self.on_outage is not None:
                try:
                    await self.on_outage(spec, eid)
                except Exception:
                    log.exception("outage hook failed", extra={"target_id": t.target_id})
        elif t.new == DEGRADED:
            eid = await asyncio.to_thread(
                self.events.open,
                LOSS,
                "warning",
                t.ts_ms,
                f"Packet loss on {spec.name}",
                target_id=t.target_id,
                details={**base, "loss_pct": round(t.loss_pct or 0, 1)},
            )
            self._open[(t.target_id, LOSS)] = eid


def startup_events(log_store: EventLog) -> int:
    """Close events left open by a previous run, and mark this start on the timeline."""
    ts = now_ms()
    closed = log_store.close_interrupted(ts)
    log_store.instant(
        MONITOR, "info", ts, "NetWatch started", details={"closed_open_events": closed}
    )
    return closed
