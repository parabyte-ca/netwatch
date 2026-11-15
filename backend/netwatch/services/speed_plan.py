"""Speed against your plan: the nightly load test's throughput as a share of the plan's speeds.

No second test: the bufferbloat test already saturates the line in both directions each night,
so its download and upload rates are reused. Each test row carries the plan speeds and the host
link speed at the time, so a later plan change or a 1 Gb/s host port is read correctly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from statistics import median

from .events import SPEED, EventLog

log = logging.getLogger(__name__)

DAY_MS = 86_400_000
SLOW_NIGHTS = 3  # scheduled tests in a row under the threshold before an event opens
LINK_CAP_PCT = 90  # a result at or above this share of the host link speed is capped by it


def _pct(bps: float | None, plan_mbps: float | None) -> float | None:
    if not bps or not plan_mbps:
        return None
    return round(bps / 1e6 / plan_mbps * 100, 1)


def point(row: dict, current: dict) -> dict:
    """One test as Mb/s and % of the plan in force when it ran (else today's plan)."""
    ctx = row.get("details", {}).get("context") or {}
    down_plan = ctx.get("plan_down_mbps") or current.get("plan_down_mbps") or None
    up_plan = ctx.get("plan_up_mbps") or current.get("plan_up_mbps") or None
    link = ctx.get("link_mbps")
    down_mbps = round(row["down_bps"] / 1e6, 1) if row.get("down_bps") else None
    up_mbps = round(row["up_bps"] / 1e6, 1) if row.get("up_bps") else None
    capped = bool(
        link and any(m is not None and m >= link * LINK_CAP_PCT / 100 for m in (down_mbps, up_mbps))
    )
    return {
        "ts_ms": row["ts_ms"],
        "trigger": row.get("trigger"),
        "down_mbps": down_mbps,
        "up_mbps": up_mbps,
        "down_pct": _pct(row.get("down_bps"), down_plan),
        "up_pct": _pct(row.get("up_bps"), up_plan),
        "plan_down_mbps": down_plan,
        "plan_up_mbps": up_plan,
        "link_mbps": link,
        "link_capped": capped,
    }


def _med(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(median(vals), 1) if vals else None


class SpeedPlan:
    def __init__(
        self,
        tests: Callable[[int], list[dict]],
        events: EventLog,
        plan: Callable[[], dict],
        kv_get: Callable[[str, object], object],
        kv_set: Callable[[str, object], None],
    ) -> None:
        self.tests = tests  # (limit) -> bufferbloat rows, newest first
        self.events = events
        self.plan = plan  # -> {"plan_down_mbps", "plan_up_mbps", "plan_slow_pct"}
        self.kv_get, self.kv_set = kv_get, kv_set

    def _points(self, start_ms: int, end_ms: int) -> list[dict]:
        current = self.plan()
        rows = [
            r for r in self.tests(2000) if r["status"] == "ok" and start_ms <= r["ts_ms"] < end_ms
        ]
        return [point(r, current) for r in sorted(rows, key=lambda r: r["ts_ms"])]

    def view(self, days: int = 90, now_ms: int | None = None) -> dict:
        now = now_ms or int(time.time() * 1000)
        plan = self.plan()
        pts = self._points(now - days * DAY_MS, now + 1)
        recent = [p for p in pts if p["ts_ms"] >= now - 30 * DAY_MS]
        return {
            "plan": plan,
            "set": bool(plan.get("plan_down_mbps") or plan.get("plan_up_mbps")),
            "points": pts,
            "last": pts[-1] if pts else None,
            "median_30d": {
                "down_mbps": _med([p["down_mbps"] for p in recent]),
                "up_mbps": _med([p["up_mbps"] for p in recent]),
                "down_pct": _med([p["down_pct"] for p in recent]),
                "up_pct": _med([p["up_pct"] for p in recent]),
                "tests": len(recent),
            },
            "open_event_id": self._open_event(),
        }

    def period(self, start_ms: int, end_ms: int) -> dict | None:
        """For the score and the weekly report: medians over a period, when a plan is set."""
        pts = [p for p in self._points(start_ms, end_ms) if p["down_pct"] or p["up_pct"]]
        if not pts:
            return None
        return {
            "tests": len(pts),
            "down_pct": _med([p["down_pct"] for p in pts]),
            "up_pct": _med([p["up_pct"] for p in pts]),
            "down_mbps": _med([p["down_mbps"] for p in pts]),
            "up_mbps": _med([p["up_mbps"] for p in pts]),
            "link_capped": any(p["link_capped"] for p in pts),
        }

    # ── slow nights ──────────────────────────────────────────────────────────

    def _open_event(self) -> int | None:
        eid = self.kv_get("speed_plan_event", None)
        return eid if isinstance(eid, int) else None

    def check(self, now_ms: int | None = None) -> dict | None:
        """After a scheduled test: open or close "Internet slower than your plan"."""
        now = now_ms or int(time.time() * 1000)
        thr = self.plan().get("plan_slow_pct") or 70
        nights = [p for p in reversed(self._points(now - 30 * DAY_MS, now + 1))
                  if p["trigger"] == "scheduled"]  # fmt: skip

        def slow(p: dict) -> bool | None:
            pcts = [x for x in (p["down_pct"], p["up_pct"]) if x is not None]
            return min(pcts) < thr if pcts else None

        open_id = self._open_event()
        if not nights or slow(nights[0]) is None:
            return None
        if open_id is not None:
            if not slow(nights[0]):
                self.events.close(open_id, nights[0]["ts_ms"])
                self.kv_set("speed_plan_event", None)
                return {"action": "closed", "event_id": open_id}
            return None
        run = []
        for p in nights:
            if not slow(p):
                break
            run.append(p)
        if len(run) < SLOW_NIGHTS:
            return None
        first = run[-1]
        down = _med([p["down_pct"] for p in run])
        up = _med([p["up_pct"] for p in run])
        capped = any(p["link_capped"] for p in run)
        title = f"Internet slower than your plan: {len(run)} nights under {thr} %"
        eid = self.events.open(
            SPEED,
            "info",
            first["ts_ms"],
            title,
            details={
                "nights": len(run),
                "threshold_pct": thr,
                "down_pct": down,
                "up_pct": up,
                "link_capped": capped,
                "hint": (
                    "The NetWatch host's network port was the limit on some nights, so the line "
                    "may be fine; test from a faster device to compare."
                    if capped
                    else "Median of the nightly tests from the NetWatch host. Worth raising with "
                    "your provider if it persists; the Health page has the 90-day chart."
                ),
            },
        )
        self.kv_set("speed_plan_event", eid)
        return {"action": "opened", "event_id": eid}
