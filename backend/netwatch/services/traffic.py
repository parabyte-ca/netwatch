"""Per-device traffic: who is using the bandwidth, now and over the last days.

Rates come from the Deco API (each client's current download/upload speed, read every poll).
They are stored per MAC and minute (average and peak) for ``traffic_retention_days``, then as
hourly byte totals for ``rollup_retention_days``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import device_traffic, device_traffic_1h

HOUR_WATERMARK_KEY = "traffic_hour_watermark"


class TrafficStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, rates: dict[str, tuple[float, float]], now_ms: int) -> int:
        """Fold one poll's rates (mac → (down_bps, up_bps)) into this minute's rows."""
        rows = [
            {
                "mac": mac,
                "bucket_ts": now_ms // 60_000 * 60,
                "down_bps": d,
                "up_bps": u,
                "down_peak": d,
                "up_peak": u,
                "n": 1,
            }
            for mac, (d, u) in rates.items()
            if d or u
        ]
        if not rows:
            return 0
        t = device_traffic.c
        ins = sqlite_insert(device_traffic)
        ex = ins.excluded
        ins = ins.on_conflict_do_update(
            index_elements=[t.mac, t.bucket_ts],
            set_={
                "down_bps": (t.down_bps * t.n + ex.down_bps) / (t.n + 1),
                "up_bps": (t.up_bps * t.n + ex.up_bps) / (t.n + 1),
                "down_peak": func.max(t.down_peak, ex.down_peak),
                "up_peak": func.max(t.up_peak, ex.up_peak),
                "n": t.n + 1,
            },
        )
        with self.engine.begin() as conn:
            conn.execute(ins, rows)
        return len(rows)

    def rollup_hours(self, now_s: int, *, since_s: int) -> int:
        """Hourly byte totals for every complete hour from since_s (idempotent)."""
        end = now_s // 3600 * 3600
        start = since_s // 3600 * 3600
        if start >= end:
            return 0
        t = device_traffic.c
        stmt = (
            select(
                t.mac,
                (t.bucket_ts - t.bucket_ts % 3600).label("h"),
                func.sum(t.down_bps * 60 / 8),
                func.sum(t.up_bps * 60 / 8),
                func.max(t.down_peak),
                func.max(t.up_peak),
            )
            .where(t.bucket_ts >= start, t.bucket_ts < end)
            .group_by(t.mac, "h")
        )
        with self.engine.connect() as conn:
            rows = [
                {
                    "mac": m,
                    "bucket_ts": h,
                    "down_bytes": d,
                    "up_bytes": u,
                    "down_peak": dp,
                    "up_peak": up,
                }
                for m, h, d, u, dp, up in conn.execute(stmt)
            ]
        if not rows:
            return 0
        h = device_traffic_1h.c
        ins = sqlite_insert(device_traffic_1h)
        ins = ins.on_conflict_do_update(
            index_elements=[h.mac, h.bucket_ts],
            set_={k: ins.excluded[k] for k in ("down_bytes", "up_bytes", "down_peak", "up_peak")},
        )
        with self.engine.begin() as conn:
            conn.execute(ins, rows)
        return len(rows)

    def purge(self, minute_cutoff_s: int, hour_cutoff_s: int) -> int:
        with self.engine.begin() as conn:
            n = conn.execute(
                delete(device_traffic).where(device_traffic.c.bucket_ts < minute_cutoff_s)
            ).rowcount
            n += conn.execute(
                delete(device_traffic_1h).where(device_traffic_1h.c.bucket_ts < hour_cutoff_s)
            ).rowcount
        return n

    def totals(self, start_s: int, end_s: int, *, minute_floor_s: int) -> dict[str, dict]:
        """Bytes per MAC in [start_s, end_s): minute rows where kept, hourly totals before."""
        out: dict[str, dict] = defaultdict(
            lambda: {"down_bytes": 0.0, "up_bytes": 0.0, "down_peak": 0.0, "up_peak": 0.0}
        )
        split = max(start_s, minute_floor_s)
        t, h = device_traffic.c, device_traffic_1h.c
        queries = []
        if split < end_s:
            queries.append(
                select(
                    t.mac,
                    func.sum(t.down_bps * 60 / 8),
                    func.sum(t.up_bps * 60 / 8),
                    func.max(t.down_peak),
                    func.max(t.up_peak),
                )
                .where(t.bucket_ts >= split, t.bucket_ts < end_s)
                .group_by(t.mac)
            )
        if start_s < split:
            queries.append(
                select(
                    h.mac,
                    func.sum(h.down_bytes),
                    func.sum(h.up_bytes),
                    func.max(h.down_peak),
                    func.max(h.up_peak),
                )
                .where(h.bucket_ts >= start_s, h.bucket_ts < split)
                .group_by(h.mac)
            )
        with self.engine.connect() as conn:
            for q in queries:
                for mac, d, u, dp, up in conn.execute(q):
                    o = out[mac]
                    o["down_bytes"] += d or 0
                    o["up_bytes"] += u or 0
                    o["down_peak"] = max(o["down_peak"], dp or 0)
                    o["up_peak"] = max(o["up_peak"], up or 0)
        return dict(out)

    def series(
        self, macs: Iterable[str], start_s: int, end_s: int, step_s: int, *, minute_floor_s: int
    ) -> list[dict]:
        """Average rates of a device (all its MACs) in step_s buckets."""
        macs = list(macs)
        buckets: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        t, h = device_traffic.c, device_traffic_1h.c
        with self.engine.connect() as conn:
            if end_s > minute_floor_s:
                q = select(t.bucket_ts, t.down_bps, t.up_bps, t.down_peak, t.up_peak).where(
                    t.mac.in_(macs),
                    t.bucket_ts >= max(start_s, minute_floor_s),
                    t.bucket_ts < end_s,
                )
                for ts, d, u, dp, up in conn.execute(q):
                    b = buckets[(ts - start_s) // step_s * step_s + start_s]
                    b[0] += d * 60
                    b[1] += u * 60
                    b[2], b[3] = max(b[2], dp), max(b[3], up)
            if start_s < minute_floor_s:
                q = select(h.bucket_ts, h.down_bytes, h.up_bytes, h.down_peak, h.up_peak).where(
                    h.mac.in_(macs),
                    h.bucket_ts >= start_s,
                    h.bucket_ts < min(end_s, minute_floor_s),
                )
                for ts, d, u, dp, up in conn.execute(q):
                    b = buckets[(ts - start_s) // step_s * step_s + start_s]
                    b[0] += d * 8
                    b[1] += u * 8
                    b[2], b[3] = max(b[2], dp), max(b[3], up)
        return [
            {
                "ts": ts,
                "down_bps": b[0] / step_s,
                "up_bps": b[1] / step_s,
                "down_peak": b[2],
                "up_peak": b[3],
            }
            for ts, b in sorted(buckets.items())
        ]
