"""Latency statistics: per-bucket aggregation of raw samples and merging of rollups.

Raw aggregation is exact. Merging rollups into coarser buckets is exact for count, loss,
min, max, avg and jitter; percentiles are approximated by a success-weighted mean of the
source percentiles, which is the usual trade-off for pre-aggregated latency data.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence

FIELDS = ("count", "lost", "min", "p5", "p25", "median", "p75", "p95", "max", "avg", "jitter")
_PCTS = (("p5", 5), ("p25", 25), ("median", 50), ("p75", 75), ("p95", 95))
_WEIGHTED = ("p5", "p25", "median", "p75", "p95", "avg", "jitter")


def percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile of an already sorted, non-empty sequence."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[lo]
    frac = rank - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def aggregate(rtts: Iterable[float | None]) -> dict:
    """Aggregate an ordered series of RTTs (None = lost probe) into one bucket."""
    series = list(rtts)
    ok = [r for r in series if r is not None]
    out: dict = {"count": len(series), "lost": len(series) - len(ok)}
    if not ok:
        out.update({k: None for k in FIELDS[2:]})
        return out
    s = sorted(ok)
    out["min"] = s[0]
    out["max"] = s[-1]
    out["avg"] = sum(ok) / len(ok)
    for name, pct in _PCTS:
        out[name] = percentile(s, pct)
    # Jitter as mean absolute difference between consecutive successful replies (RFC 3550 style,
    # without the exponential smoothing).
    out["jitter"] = (
        sum(abs(b - a) for a, b in itertools.pairwise(ok)) / (len(ok) - 1) if len(ok) > 1 else 0.0
    )
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out.items()}


def merge(buckets: Iterable[dict]) -> dict:
    """Merge several aggregated buckets into one."""
    items = list(buckets)
    count = sum(b["count"] for b in items)
    lost = sum(b["lost"] for b in items)
    out: dict = {"count": count, "lost": lost}
    ok_items = [b for b in items if b["count"] - b["lost"] > 0 and b["min"] is not None]
    if not ok_items:
        out.update({k: None for k in FIELDS[2:]})
        return out
    weights = [b["count"] - b["lost"] for b in ok_items]
    out["min"] = min(b["min"] for b in ok_items)
    out["max"] = max(b["max"] for b in ok_items)
    for key in _WEIGHTED:
        vals = [(b[key], w) for b, w in zip(ok_items, weights, strict=True) if b[key] is not None]
        wsum = sum(w for _, w in vals)
        out[key] = round(sum(v * w for v, w in vals) / wsum, 3) if wsum else None
    return out


def loss_pct(bucket: dict) -> float | None:
    if not bucket["count"]:
        return None
    return round(100.0 * bucket["lost"] / bucket["count"], 2)
