"""AdGuard Home queries per client and hour: counts for 90 days, domain names for 7.

The current hour is built in memory from each poll's query-log entries (``ClientAccumulator``)
and written back every poll, so the device page is at most a minute behind.
"""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import Engine, and_, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ...db import adguard_clients

HOUR_MS = 3_600_000
TOP_DOMAINS = 30  # answered names kept per client and hour
TOP_BLOCKED = 15  # blocked names kept per client and hour


def hour_of(ts_ms: int) -> int:
    return ts_ms - ts_ms % HOUR_MS


def _top(counter: Counter, n: int) -> dict[str, int]:
    return dict(counter.most_common(n))


def _merge(rows: list[dict], key: str, n: int) -> list[dict]:
    total: Counter[str] = Counter()
    for r in rows:
        if r.get(key):
            total.update(json.loads(r[key]))
    return [{"domain": d, "queries": c} for d, c in total.most_common(n)]


class ClientStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def put(self, rows: list[dict]) -> None:
        """Insert or replace whole hour rows (``domains`` as dicts)."""
        if not rows:
            return
        values = [
            {
                **r,
                "domains": json.dumps(r["domains"]) if r.get("domains") is not None else None,
                "blocked_domains": json.dumps(r["blocked_domains"])
                if r.get("blocked_domains") is not None
                else None,
            }
            for r in rows
        ]
        stmt = sqlite_insert(adguard_clients).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["hour_ms", "client"],
            set_={c: stmt.excluded[c] for c in (
                "device_id", "queries", "blocked", "failed", "domains", "blocked_domains")},
        )  # fmt: skip
        with self.engine.begin() as conn:
            conn.execute(stmt)

    def hour(self, hour_ms: int) -> list[dict]:
        stmt = select(adguard_clients).where(adguard_clients.c.hour_ms == hour_ms)
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(stmt)]
        for r in rows:
            for k in ("domains", "blocked_domains"):
                r[k] = json.loads(r[k]) if r[k] else {}
        return rows

    def _rows(self, where, start_ms: int, end_ms: int) -> list[dict]:
        stmt = (
            select(adguard_clients)
            .where(where, adguard_clients.c.hour_ms >= hour_of(start_ms))
            .where(adguard_clients.c.hour_ms < end_ms)
            .order_by(adguard_clients.c.hour_ms)
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def summary(self, device_ids: list[int], start_ms: int, end_ms: int) -> dict:
        """Totals, hourly counts and top domains for one device (its merged MACs included)."""
        rows = self._rows(adguard_clients.c.device_id.in_(device_ids or [-1]), start_ms, end_ms)
        hourly: dict[int, dict] = {}
        for r in rows:
            h = hourly.setdefault(
                r["hour_ms"], {"hour_ms": r["hour_ms"], "queries": 0, "blocked": 0}
            )
            h["queries"] += r["queries"]
            h["blocked"] += r["blocked"]
        q = sum(r["queries"] for r in rows)
        b = sum(r["blocked"] for r in rows)
        f = sum(r["failed"] for r in rows)
        return {
            "queries": q,
            "blocked_pct": round(100 * b / q, 1) if q else None,
            "failed_pct": round(100 * f / max(1, q - b), 1) if q else None,
            "hourly": list(hourly.values()),
            "top_domains": _merge(rows, "domains", 15),
            "top_blocked": _merge(rows, "blocked_domains", 10),
        }

    def network(self, start_ms: int, end_ms: int) -> dict:
        """Top domains over every client, and clients that match no device."""
        rows = self._rows(adguard_clients.c.hour_ms >= 0, start_ms, end_ms)
        unknown: Counter[str] = Counter()
        for r in rows:
            if r["device_id"] is None:
                unknown[r["client"]] += r["queries"]
        return {
            "top_domains": _merge(rows, "domains", 15),
            "top_blocked": _merge(rows, "blocked_domains", 10),
            "unknown_clients": [{"client": c, "queries": n} for c, n in unknown.most_common(20)],
        }

    def device_hours(self, start_ms: int) -> dict[int, list[tuple[int, int]]]:
        """(queries, blocked) per device and finished hour since ``start_ms``, for baselines."""
        stmt = (
            select(
                adguard_clients.c.device_id,
                adguard_clients.c.hour_ms,
                func.sum(adguard_clients.c.queries),
                func.sum(adguard_clients.c.blocked),
            )
            .where(adguard_clients.c.device_id.is_not(None))
            .where(adguard_clients.c.hour_ms >= start_ms)
            .group_by(adguard_clients.c.device_id, adguard_clients.c.hour_ms)
        )
        out: dict[int, list[tuple[int, int]]] = {}
        with self.engine.connect() as conn:
            for dev, _h, q, b in conn.execute(stmt):
                out.setdefault(dev, []).append((int(q), int(b)))
        return out

    def purge(self, cutoff_ms: int) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                delete(adguard_clients).where(adguard_clients.c.hour_ms < cutoff_ms)
            ).rowcount

    def strip_domains(self, cutoff_ms: int) -> int:
        """Forget which names were looked up before ``cutoff_ms`` (keeps the counts)."""
        stmt = (
            update(adguard_clients)
            .where(
                and_(
                    adguard_clients.c.hour_ms < cutoff_ms,
                    (adguard_clients.c.domains.is_not(None))
                    | (adguard_clients.c.blocked_domains.is_not(None)),
                )
            )
            .values(domains=None, blocked_domains=None)
        )
        with self.engine.begin() as conn:
            return conn.execute(stmt).rowcount
