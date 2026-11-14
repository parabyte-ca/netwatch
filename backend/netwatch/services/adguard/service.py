"""AdGuard Home integration: reads the LAN's DNS resolver every minute (read-only).

Records query volume, block rate, errors, latency and per-upstream health, and turns problems
into "dns" timeline events the diagnosis uses as evidence: "AdGuard Home is unreachable", "an
upstream resolver is failing", "DNS errors spiked".
"""

from __future__ import annotations

import json

from sqlalchemy import Engine, delete, insert, select

from ...db import adguard_stats


def _row(r) -> dict:
    d = dict(r._mapping)
    d["upstreams"] = json.loads(d.get("upstreams") or "{}")
    d["details"] = json.loads(d.get("details") or "{}")
    return d


class AdGuardStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def add(self, row: dict) -> None:
        values = {
            **row,
            "upstreams": json.dumps(row.get("upstreams") or {}),
            "details": json.dumps(row.get("details") or {}),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(adguard_stats).values(**values))

    def history(self, start_ms: int, end_ms: int, limit: int = 20_000) -> list[dict]:
        stmt = (
            select(adguard_stats)
            .where(adguard_stats.c.ts_ms >= start_ms, adguard_stats.c.ts_ms < end_ms)
            .order_by(adguard_stats.c.ts_ms)
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(stmt)]

    def latest(self) -> dict | None:
        stmt = select(adguard_stats).order_by(adguard_stats.c.ts_ms.desc()).limit(1)
        with self.engine.connect() as conn:
            r = conn.execute(stmt).first()
        return _row(r) if r else None

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(adguard_stats).where(adguard_stats.c.ts_ms < cutoff_ms)
            ).rowcount
