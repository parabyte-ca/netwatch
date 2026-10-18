"""Time-series queries for graphs: picks raw samples or rollups depending on the range."""

from __future__ import annotations

import math
from collections import defaultdict

from .stats import FIELDS, aggregate, loss_pct, merge
from .storage import Store

RAW_MAX_RANGE_S = 6 * 3600
SERIES_FIELDS = ("ts", *FIELDS, "loss_pct")


_EMPTY = aggregate([])


def _fill_gaps(buckets: dict[int, dict], start: int, end: int, step: int) -> list[tuple[int, dict]]:
    """Emit every bucket in [start, end) so periods without samples render as gaps, not lines."""
    return [(ts, buckets.get(ts, _EMPTY)) for ts in range(start, end, step)]


def _columns(buckets: list[tuple[int, dict]]) -> dict[str, list]:
    cols: dict[str, list] = {k: [] for k in SERIES_FIELDS}
    for ts, b in buckets:
        cols["ts"].append(ts)
        for k in FIELDS:
            cols[k].append(b[k])
        cols["loss_pct"].append(loss_pct(b))
    return cols


def build_series(
    store: Store,
    target: dict,
    start: int,
    end: int,
    *,
    now: float,
    raw_retention_days: int,
    points: int = 720,
) -> dict:
    span = max(end - start, 1)
    use_raw = span <= RAW_MAX_RANGE_S and start >= now - raw_retention_days * 86400

    if use_raw:
        step = max(math.ceil(target["interval_s"]), math.ceil(span / points))
        groups: dict[int, list[float | None]] = defaultdict(list)
        all_rtts: list[float | None] = []
        for ts_ms, rtt, ok, _err in store.raw_samples(target["id"], start * 1000, end * 1000):
            value = rtt if ok else None
            groups[(ts_ms // 1000 - start) // step * step + start].append(value)
            all_rtts.append(value)
        buckets = _fill_gaps({ts: aggregate(v) for ts, v in groups.items()}, start, end, step)
        summary = aggregate(all_rtts)
        source = "raw"
    else:
        step = max(60, math.ceil(span / points / 60) * 60)
        start = start // 60 * 60
        grouped = store.rollup_buckets(target["id"], start, end, step)  # merged in SQL
        buckets = _fill_gaps(grouped, start, end, step)
        summary = merge(grouped.values())
        source = "rollup"

    summary["loss_pct"] = loss_pct(summary)
    return {
        "target_id": target["id"],
        "start": start,
        "end": end,
        "step": step,
        "source": source,
        "series": _columns(buckets),
        "summary": summary,
    }
