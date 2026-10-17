"""iperf3 throughput tests: LAN (TrueNAS → a device running `iperf3 -s`) and WAN (public server).

Comparing the two isolates the LAN from the ISP: a fast LAN test with a slow WAN test points at
Bell; a slow LAN test to a wired PC points at the switch/cabling; slow only over Wi-Fi points at
the mesh. Each run measures download (server → NetWatch, iperf3 -R) then upload, one at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from sqlalchemy import Engine, delete, insert, select, update

from ..db import iperf_servers, throughput_tests
from .pathtest import Runner, run_process

log = logging.getLogger(__name__)


def parse_iperf_json(text: str) -> dict:
    """Extract the headline numbers from `iperf3 -J`; raises ValueError on an iperf3 error."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("unexpected iperf3 output")
    if data.get("error"):
        raise ValueError(str(data["error"])[:200])
    end = data.get("end") or {}
    received = end.get("sum_received") or {}
    sent = end.get("sum_sent") or {}
    bps = received.get("bits_per_second") or sent.get("bits_per_second")
    if bps is None:
        raise ValueError("iperf3 reported no throughput")
    return {
        "bits_per_second": float(bps),
        "retransmits": sent.get("retransmits"),
        "duration_s": received.get("seconds") or sent.get("seconds"),
        "details": {
            "sender_bps": sent.get("bits_per_second"),
            "receiver_bps": received.get("bits_per_second"),
            "cpu": end.get("cpu_utilization_percent"),
            "remote_host": (data.get("start") or {}).get("connecting_to", {}).get("host"),
        },
    }


class ThroughputStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # servers
    def list_servers(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [
                dict(r._mapping)
                for r in conn.execute(select(iperf_servers).order_by(iperf_servers.c.id))
            ]

    def get_server(self, server_id: int) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(iperf_servers).where(iperf_servers.c.id == server_id)).first()
        return dict(r._mapping) if r else None

    def create_server(self, values: dict) -> dict:
        now = time.time()
        with self.engine.begin() as conn:
            sid = conn.execute(
                insert(iperf_servers).values(**values, created_at=now, updated_at=now)
            ).inserted_primary_key[0]
        return self.get_server(sid)

    def update_server(self, server_id: int, values: dict) -> dict | None:
        with self.engine.begin() as conn:
            n = conn.execute(
                update(iperf_servers)
                .where(iperf_servers.c.id == server_id)
                .values(**values, updated_at=time.time())
            ).rowcount
        return self.get_server(server_id) if n else None

    def delete_server(self, server_id: int) -> bool:
        with self.engine.begin() as conn:
            return (
                conn.execute(delete(iperf_servers).where(iperf_servers.c.id == server_id)).rowcount
                > 0
            )

    # results
    def insert_test(self, row: dict) -> dict:
        row = {**row, "details": json.dumps(row.get("details") or {})}
        with self.engine.begin() as conn:
            tid = conn.execute(insert(throughput_tests).values(**row)).inserted_primary_key[0]
            r = conn.execute(select(throughput_tests).where(throughput_tests.c.id == tid)).first()
        return self._row(r)

    def list_tests(
        self, *, server_id: int | None = None, start_ms: int | None = None, limit: int = 100
    ) -> list[dict]:
        stmt = select(throughput_tests)
        if server_id is not None:
            stmt = stmt.where(throughput_tests.c.server_id == server_id)
        if start_ms is not None:
            stmt = stmt.where(throughput_tests.c.ts_ms >= start_ms)
        stmt = stmt.order_by(throughput_tests.c.ts_ms.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [self._row(r) for r in conn.execute(stmt)]

    def last_run_ms(self, server_id: int) -> int | None:
        stmt = select(throughput_tests.c.ts_ms).where(throughput_tests.c.server_id == server_id)
        stmt = stmt.order_by(throughput_tests.c.ts_ms.desc()).limit(1)
        with self.engine.connect() as conn:
            return conn.execute(stmt).scalar()

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(throughput_tests).where(throughput_tests.c.ts_ms < cutoff_ms)
            ).rowcount

    @staticmethod
    def _row(r) -> dict:
        d = dict(r._mapping)
        d["details"] = json.loads(d["details"] or "{}")
        return d


class ThroughputTester:
    def __init__(self, store: ThroughputStore, *, runner: Runner = run_process) -> None:
        self.store = store
        self.runner = runner
        self._lock = asyncio.Lock()  # one test at a time: parallel tests skew each other
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def running(self) -> bool:
        return self._task is None or not self._task.done()

    async def run_server(self, server: dict, *, trigger: str) -> list[dict]:
        async with self._lock:
            results = []
            for direction in ("download", "upload"):
                results.append(await self._run_one(server, direction, trigger))
            return results

    async def _run_one(self, server: dict, direction: str, trigger: str) -> dict:
        cmd = [
            "iperf3", "-c", server["host"], "-p", str(server["port"]),
            "-t", str(server["duration_s"]), "-P", str(server["parallel"]),
            "-J", "--connect-timeout", "5000",
        ]  # fmt: skip
        if direction == "download":
            cmd.append("-R")
        started = time.time()
        try:
            rc, out, err = await self.runner(cmd, server["duration_s"] + 30)
        except FileNotFoundError:
            rc, out, err = 127, "", "iperf3 is not installed"
        row = {
            "ts_ms": int(started * 1000),
            "server_id": server["id"],
            "host": server["host"],
            "port": server["port"],
            "kind": server["kind"],
            "direction": direction,
            "trigger": trigger,
        }
        try:
            if not out.strip():
                raise ValueError(err.strip() or f"iperf3 exited {rc}")
            parsed = parse_iperf_json(out)
            row.update(status="ok", **parsed)
        except ValueError as exc:
            row.update(status="error", error=str(exc)[:200])
        return await asyncio.to_thread(self.store.insert_test, row)

    def start_schedule(self) -> None:
        async def loop() -> None:
            await asyncio.sleep(120)
            while True:
                try:
                    await self._scheduled_pass()
                except Exception:
                    log.exception("throughput schedule pass failed")
                await asyncio.sleep(60)

        self._task = asyncio.create_task(loop(), name="netwatch-throughput")

    async def _scheduled_pass(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        ran = 0
        for s in await asyncio.to_thread(self.store.list_servers):
            if not s["enabled"] or s["interval_min"] <= 0:
                continue
            last = await asyncio.to_thread(self.store.last_run_ms, s["id"])
            if last is None or now * 1000 - last >= s["interval_min"] * 60_000:
                await self.run_server(s, trigger="scheduled")
                ran += 1
        return ran

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
