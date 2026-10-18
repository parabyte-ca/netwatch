"""Synchronous data access. Called from worker threads (FastAPI sync routes, asyncio.to_thread)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import Engine, Integer, case, cast, delete, func, insert, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import kv, rollup_1m, samples, targets
from ..probes.base import ProbeResult
from .stats import FIELDS, WEIGHTED, aggregate

log = logging.getLogger(__name__)

WATERMARK_KEY = "rollup_watermark"
_ROLLUP_CHUNK_S = 6 * 3600
LATE_SAMPLE_S = 180  # longest probe timeout (60 s) plus flush retries, rounded up


class Store:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ── key/value ────────────────────────────────────────────────────────────

    def kv_get(self, key: str, default=None):
        with self.engine.connect() as conn:
            value = conn.execute(select(kv.c.value).where(kv.c.key == key)).scalar()
        return json.loads(value) if value is not None else default

    def kv_set(self, key: str, value) -> None:
        stmt = sqlite_insert(kv).values(key=key, value=json.dumps(value))
        stmt = stmt.on_conflict_do_update(
            index_elements=[kv.c.key], set_={"value": stmt.excluded.value}
        )
        with self.engine.begin() as conn:
            conn.execute(stmt)

    # ── targets ──────────────────────────────────────────────────────────────

    def list_targets(self, *, enabled_only: bool = False) -> list[dict]:
        stmt = select(targets).order_by(targets.c.id)
        if enabled_only:
            stmt = stmt.where(targets.c.enabled.is_(True))
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def get_target(self, target_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(targets).where(targets.c.id == target_id)).first()
        return dict(row._mapping) if row else None

    def count_targets(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(targets)).scalar_one()

    def create_target(self, values: dict) -> dict:
        now = time.time()
        with self.engine.begin() as conn:
            new_id = conn.execute(
                insert(targets).values(**values, created_at=now, updated_at=now)
            ).inserted_primary_key[0]
        return self.get_target(new_id)

    def update_target(self, target_id: int, values: dict) -> dict | None:
        with self.engine.begin() as conn:
            res = conn.execute(
                update(targets)
                .where(targets.c.id == target_id)
                .values(**values, updated_at=time.time())
            )
        return self.get_target(target_id) if res.rowcount else None

    def delete_target(self, target_id: int) -> bool:
        with self.engine.begin() as conn:
            return conn.execute(delete(targets).where(targets.c.id == target_id)).rowcount > 0

    # ── samples ──────────────────────────────────────────────────────────────

    def insert_samples(self, results: Iterable[ProbeResult]) -> int:
        rows = [
            {
                "target_id": r.target_id,
                "ts_ms": r.ts_ms,
                "rtt_ms": r.rtt_ms,
                "ok": r.ok,
                "error": r.error,
            }
            for r in results
        ]
        if not rows:
            return 0
        # Targets deleted while their samples sat in the buffer would violate the foreign key.
        stmt = sqlite_insert(samples).on_conflict_do_nothing()
        live = {t["id"] for t in self.list_targets()}
        rows = [r for r in rows if r["target_id"] in live]
        if rows:
            with self.engine.begin() as conn:
                conn.execute(stmt, rows)
        return len(rows)

    def raw_samples(self, target_id: int, start_ms: int, end_ms: int) -> list[tuple]:
        stmt = (
            select(samples.c.ts_ms, samples.c.rtt_ms, samples.c.ok, samples.c.error)
            .where(
                samples.c.target_id == target_id,
                samples.c.ts_ms >= start_ms,
                samples.c.ts_ms < end_ms,
            )
            .order_by(samples.c.ts_ms)
        )
        with self.engine.connect() as conn:
            return [tuple(r) for r in conn.execute(stmt)]

    def loss_counts(self, target_id: int, start_ms: int, end_ms: int) -> tuple[int, int]:
        """(probes, lost) in [start_ms, end_ms) from raw samples, counted in SQL."""
        stmt = select(
            func.count(), func.coalesce(func.sum(case((samples.c.ok, 0), else_=1)), 0)
        ).where(
            samples.c.target_id == target_id,
            samples.c.ts_ms >= start_ms,
            samples.c.ts_ms < end_ms,
        )
        with self.engine.connect() as conn:
            n, lost = conn.execute(stmt).one()
        return int(n), int(lost)

    def rollup_loss_counts(self, target_id: int, start_s: int, end_s: int) -> tuple[int, int]:
        stmt = select(
            func.coalesce(func.sum(rollup_1m.c.count), 0),
            func.coalesce(func.sum(rollup_1m.c.lost), 0),
        ).where(
            rollup_1m.c.target_id == target_id,
            rollup_1m.c.bucket_ts >= start_s,
            rollup_1m.c.bucket_ts < end_s,
        )
        with self.engine.connect() as conn:
            n, lost = conn.execute(stmt).one()
        return int(n), int(lost)

    @staticmethod
    def _merged_columns() -> list:
        """SQL equivalents of ``stats.merge`` over rollup rows (see its docstring)."""
        r = rollup_1m.c
        ok = r.count - r.lost
        good = (ok > 0) & r.min.is_not(None)

        def weighted(col):
            num = func.sum(case((good & col.is_not(None), col * ok)))
            den = func.sum(case((good & col.is_not(None), ok)))
            return func.round(num * 1.0 / func.nullif(den, 0), 3)

        return [
            func.coalesce(func.sum(r.count), 0),
            func.coalesce(func.sum(r.lost), 0),
            func.min(case((good, r.min))),
            func.max(case((good, r.max))),
            *(weighted(getattr(r, k)) for k in WEIGHTED),
        ]

    @staticmethod
    def _merged_row(row) -> dict:
        out = {"count": int(row[0]), "lost": int(row[1]), "min": row[2], "max": row[3]}
        out.update(zip(WEIGHTED, row[4:], strict=True))
        if out["min"] is None:
            out.update({k: None for k in FIELDS[2:]})
        return out

    def rollup_merged(self, target_id: int, start_s: int, end_s: int) -> dict:
        """``stats.merge`` of the rollups in [start_s, end_s), computed in SQL (a 90-day range
        is ~130k rows: far too many to turn into Python dicts on every request)."""
        r = rollup_1m.c
        stmt = select(*self._merged_columns()).where(
            r.target_id == target_id, r.bucket_ts >= start_s, r.bucket_ts < end_s
        )
        with self.engine.connect() as conn:
            return self._merged_row(conn.execute(stmt).one())

    def rollup_buckets(self, target_id: int, start_s: int, end_s: int, step_s: int) -> dict:
        """Rollups merged into ``step_s`` buckets aligned on start_s: {bucket start: stats}."""
        r = rollup_1m.c
        bucket = (cast((r.bucket_ts - start_s) / step_s, Integer) * step_s + start_s).label("b")
        stmt = (
            select(bucket, *self._merged_columns())
            .where(r.target_id == target_id, r.bucket_ts >= start_s, r.bucket_ts < end_s)
            .group_by(bucket)
        )
        with self.engine.connect() as conn:
            return {int(row[0]): self._merged_row(row[1:]) for row in conn.execute(stmt)}

    def rollups(self, target_id: int, start_s: int, end_s: int) -> list[dict]:
        stmt = (
            select(rollup_1m)
            .where(
                rollup_1m.c.target_id == target_id,
                rollup_1m.c.bucket_ts >= start_s,
                rollup_1m.c.bucket_ts < end_s,
            )
            .order_by(rollup_1m.c.bucket_ts)
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    # ── rollups & retention ──────────────────────────────────────────────────

    def rollup_pending(self, now: float, raw_retention_days: int) -> int:
        """Aggregate every complete minute since the watermark into rollup_1m. Idempotent."""
        # A minute is complete once the writer has flushed it; leave a 15 s grace period.
        end = int((now - 15) // 60 * 60)
        floor = int(now - raw_retention_days * 86400) // 60 * 60
        watermark = self.kv_get(WATERMARK_KEY, floor)
        # Samples are stamped with the probe's start time but written when it finishes (a
        # timeout can take up to a minute, a failed flush is retried), so re-roll the last few
        # minutes every time: late samples (often the lost ones) still reach the rollups.
        start = max(min(watermark, end - LATE_SAMPLE_S), floor)
        written = 0
        while start < end:
            chunk_end = min(start + _ROLLUP_CHUNK_S, end)
            written += self._rollup_range(start, chunk_end)
            self.kv_set(WATERMARK_KEY, chunk_end)
            start = chunk_end
        return written

    def _target_ids(self, conn) -> list[int]:
        return [r[0] for r in conn.execute(select(targets.c.id))]

    def _rollup_range(self, start_s: int, end_s: int) -> int:
        groups: dict[tuple[int, int], list[float | None]] = defaultdict(list)
        with self.engine.connect() as conn:
            # One primary-key range scan per target (no timestamp-only index needed).
            for tid in self._target_ids(conn):
                stmt = select(samples.c.ts_ms, samples.c.rtt_ms, samples.c.ok).where(
                    samples.c.target_id == tid,
                    samples.c.ts_ms >= start_s * 1000,
                    samples.c.ts_ms < end_s * 1000,
                )
                for ts_ms, rtt, ok in conn.execute(stmt):
                    groups[(tid, ts_ms // 60000 * 60)].append(rtt if ok else None)
        if not groups:
            return 0
        rows = [
            {"target_id": tid, "bucket_ts": b, **aggregate(v)} for (tid, b), v in groups.items()
        ]
        ins = sqlite_insert(rollup_1m)
        ins = ins.on_conflict_do_update(
            index_elements=[rollup_1m.c.target_id, rollup_1m.c.bucket_ts],
            set_={c.name: ins.excluded[c.name] for c in rollup_1m.c if not c.primary_key},
        )
        with self.engine.begin() as conn:
            conn.execute(ins, rows)
        return len(rows)

    def purge(self, now: float, raw_retention_days: int, rollup_retention_days: int) -> dict:
        raw_cut = int((now - raw_retention_days * 86400) * 1000)
        rollup_cut = int(now - rollup_retention_days * 86400)
        raw = rolled = 0
        with self.engine.begin() as conn:
            for tid in self._target_ids(conn):  # primary-key ranges, see _rollup_range
                raw += conn.execute(
                    delete(samples).where(samples.c.target_id == tid, samples.c.ts_ms < raw_cut)
                ).rowcount
                rolled += conn.execute(
                    delete(rollup_1m).where(
                        rollup_1m.c.target_id == tid, rollup_1m.c.bucket_ts < rollup_cut
                    )
                ).rowcount
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA incremental_vacuum"))
            conn.commit()
        return {"samples": raw, "rollups": rolled}

    def optimize(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA optimize"))
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            conn.commit()

    # ── maintenance ──────────────────────────────────────────────────────────

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT 1")).scalar() == 1

    def size_bytes(self) -> int:
        with self.engine.connect() as conn:
            pages = conn.execute(text("PRAGMA page_count")).scalar() or 0
            size = conn.execute(text("PRAGMA page_size")).scalar() or 0
        return int(pages * size)


def backup_database(db_path: Path, dest: Path) -> Path:
    """Online, WAL-consistent copy using SQLite's backup API."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    tmp.replace(dest)
    return dest
