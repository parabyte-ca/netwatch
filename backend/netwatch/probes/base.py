"""Probe primitives shared by every probe type."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """Immutable snapshot of a target as the scheduler sees it."""

    id: int
    name: str
    kind: str
    host: str
    interval_s: float
    timeout_s: float
    group: str = "custom"
    port: int | None = None
    url: str | None = None
    dns_query: str | None = None
    dns_rdtype: str | None = None
    expect_status: int | None = None

    @classmethod
    def from_row(cls, row) -> TargetSpec:
        m = row._mapping if hasattr(row, "_mapping") else row
        return cls(
            id=m["id"],
            name=m["name"],
            kind=m["kind"],
            host=m["host"],
            interval_s=m["interval_s"],
            timeout_s=m["timeout_s"],
            group=m["group"],
            port=m["port"],
            url=m["url"],
            dns_query=m["dns_query"],
            dns_rdtype=m["dns_rdtype"],
            expect_status=m["expect_status"],
        )


@dataclass(frozen=True, slots=True)
class ProbeResult:
    target_id: int
    ts_ms: int
    ok: bool
    rtt_ms: float | None = None
    error: str | None = None


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class Stopwatch:
    """Monotonic timer reporting elapsed milliseconds."""

    __slots__ = ("_start",)

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000.0, 3)
