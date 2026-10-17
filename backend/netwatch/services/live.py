"""In-memory recent history per target, used for the live status view (no DB round trip)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..probes.base import ProbeResult
from .stats import aggregate, loss_pct

_MAXLEN = 600
# Consecutive failures before a target is shown as down. Phase 2 replaces this simple rule with
# the hysteresis-based outage detector.
DOWN_AFTER = 3


@dataclass
class _History:
    interval_s: float
    items: deque[ProbeResult] = field(default_factory=lambda: deque(maxlen=_MAXLEN))


class LiveState:
    def __init__(self) -> None:
        self._h: dict[int, _History] = {}

    def track(self, target_id: int, interval_s: float) -> None:
        h = self._h.get(target_id)
        if h is None:
            self._h[target_id] = _History(interval_s)
        else:
            h.interval_s = interval_s

    def forget(self, target_id: int) -> None:
        self._h.pop(target_id, None)

    def record(self, r: ProbeResult) -> None:
        h = self._h.get(r.target_id)
        if h is not None:
            h.items.append(r)

    def status(self, target_id: int, now_ms: int) -> dict:
        h = self._h.get(target_id)
        if h is None or not h.items:
            return {"state": "unknown", "last": None, "window_s": None, "stats": None}
        window_s = max(60.0, 10 * h.interval_s)
        cutoff = now_ms - window_s * 1000
        recent = [r for r in h.items if r.ts_ms >= cutoff]
        last = h.items[-1]
        fails = 0
        for r in reversed(h.items):
            if r.ok:
                break
            fails += 1
        stats = aggregate(r.rtt_ms if r.ok else None for r in recent) if recent else None
        if stats is not None:
            stats["loss_pct"] = loss_pct(stats)
        stale = now_ms - last.ts_ms > max(3 * h.interval_s, 15) * 1000
        if stale:
            state = "unknown"
        elif fails >= DOWN_AFTER or (fails and fails == len(h.items)):
            state = "down"
        elif stats and stats["lost"]:
            state = "degraded"
        else:
            state = "up"
        return {
            "state": state,
            "consecutive_failures": fails,
            "last": {
                "ts_ms": last.ts_ms,
                "ok": last.ok,
                "rtt_ms": last.rtt_ms,
                "error": last.error,
            },
            "window_s": window_s,
            "stats": stats,
        }
