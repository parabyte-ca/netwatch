"""Integration health: is every feed NetWatch relies on still reading?

One row per source (Deco, Home Hub, AdGuard Home, packet capture, the host NIC, the nightly
load test and exposure scan, the weekly report and the update helper), with the last good read,
the last error, successful reads over 24 h and the fix that applies. A source that has been stale
for 30 minutes opens one information event; it closes when the source reads again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .events import INTEGRATION, EventLog

log = logging.getLogger(__name__)

TICK_S = 15
DAY_MS = 86_400_000
NIGHTLY_STALE_MS = 26 * 3600_000  # a nightly job missing for more than a day
WEEKLY_STALE_MS = 8 * DAY_MS
NIC_GRACE_MS = 2 * 60_000  # the NIC monitor's first reading after a restart
EVENT_AFTER_MS = 30 * 60_000  # stale this long before it goes on the timeline
EVENTS_KEY = "integration_health_events"  # {source key: event id}

# States, worst first: what the UI sorts and colours by.
STALE, PAUSED, WAITING, NOT_POLLING, OK, OFF = (
    "stale",
    "paused",
    "waiting",
    "not_polling",
    "ok",
    "off",
)
PROBLEMS = {STALE, PAUSED}


@dataclass
class Poller:
    """A polling integration: the Deco, Home Hub and AdGuard Home services share these fields."""

    key: str
    label: str
    service: object
    poll_s: int
    admin_anchor: str


class IntegrationHealth:
    def __init__(
        self,
        pollers: list[Poller],
        events: EventLog,
        kv_get: Callable[[str, object], object],
        kv_set: Callable[[str, object], None],
        *,
        capture: Callable[[], dict] | None = None,
        nic: Callable[[], list[dict]] | None = None,
        nightly: Callable[[], list[dict]] = list,
        weekly: Callable[[], int | None] | None = None,
        helper: Callable[[], dict] | None = None,
        started_ms: int | None = None,
    ) -> None:
        self.pollers = pollers
        self.events = events
        self.kv_get, self.kv_set = kv_get, kv_set
        self.capture, self.nic, self.nightly = capture, nic, nightly
        self.weekly, self.helper = weekly, helper
        self.started_ms = started_ms or int(time.time() * 1000)
        # Per poller: (poll ts, ok) over the last 24 h, from watching last_poll_ms change.
        self._reads: dict[str, deque[tuple[int, bool]]] = {p.key: deque() for p in pollers}
        self._last_seen_poll: dict[str, int | None] = {}
        self._stale_since: dict[str, int] = {}
        self._task: asyncio.Task | None = None

    # ── reading the sources ──────────────────────────────────────────────────

    def _sample(self, now: int) -> None:
        for p in self.pollers:
            s = p.service
            last_poll = getattr(s, "last_poll_ms", None)
            if last_poll and last_poll != self._last_seen_poll.get(p.key):
                self._last_seen_poll[p.key] = last_poll
                self._reads[p.key].append((last_poll, getattr(s, "last_ok_ms", None) == last_poll))
            q = self._reads[p.key]
            while q and now - q[0][0] > DAY_MS:
                q.popleft()

    def _poller_row(self, p: Poller, now: int) -> dict:
        s = p.service
        configured = bool(getattr(s, "configured", False))
        cfg = s.config() if configured and hasattr(s, "config") else {}
        enabled = bool(cfg.get("enabled"))
        paused = getattr(s, "paused", None)
        last_ok = getattr(s, "last_ok_ms", None)
        error = getattr(s, "error", None)
        window = 3 * p.poll_s * 1000
        fix = None
        if not configured:
            state, detail = OFF, "Not set up."
        elif not enabled:
            state, detail = NOT_POLLING, "Saved, but polling is off, so nothing is read."
            fix = {"action": "polling", "label": "Turn on polling"}
        elif paused:
            state, detail = PAUSED, f"Stopped: {paused}. Save the settings again to resume."
            fix = {"action": "admin", "label": "Open settings", "anchor": p.admin_anchor}
        elif last_ok and now - last_ok <= window:
            state, detail = OK, f"Polled every {p.poll_s} s."
        elif not last_ok and now - self.started_ms <= window:
            state, detail = WAITING, "Started; the first read is on its way."
        else:
            state = STALE
            detail = f"No good read for {_ago(now, last_ok)}." if last_ok else "Never read."
            fix = {"action": "admin", "label": "Open settings", "anchor": p.admin_anchor}
        reads = self._reads[p.key]
        return {
            "key": p.key,
            "label": p.label,
            "state": state,
            "detail": detail,
            "error": error if state in (STALE, PAUSED) or (error and state == OK) else None,
            "last_ok_ms": last_ok,
            "reads_24h": {"ok": sum(1 for _, ok in reads if ok), "total": len(reads)}
            if configured and enabled
            else None,
            "fix": fix,
        }

    def _capture_row(self, now: int) -> dict | None:
        if self.capture is None:
            return None
        c = self.capture() or {}
        row = {
            "key": "capture",
            "label": "Packet capture",
            "last_ok_ms": None,
            "reads_24h": None,
            "fix": None,
            "error": None,
        }
        if not c.get("enabled"):
            return {**row, "state": OFF, "detail": "Passive discovery is off (Admin settings)."}
        if c.get("running"):
            return {**row, "state": OK, "detail": f"{c.get('frames', 0):,} frames read."}
        return {
            **row,
            "state": STALE,
            "error": c.get("error"),
            "detail": "Not capturing: the container needs the NET_RAW capability and host "
            "networking.",
        }

    def _nic_row(self, now: int) -> dict | None:
        if self.nic is None:
            return None
        ifaces = [i for i in (self.nic() or []) if i.get("present")]
        row = {
            "key": "nic",
            "label": "Host network port",
            "last_ok_ms": None,
            "reads_24h": None,
            "fix": None,
            "error": None,
        }
        up = [i for i in ifaces if i.get("carrier")]
        if up:
            speeds = ", ".join(f"{i['iface']} {i['speed']} Mb/s" for i in up if i.get("speed"))
            return {**row, "state": OK, "detail": f"Link up ({speeds or up[0]['iface']})."}
        if now - self.started_ms <= NIC_GRACE_MS:
            return {**row, "state": WAITING, "detail": "Started; reading the interfaces."}
        if ifaces:
            return {**row, "state": STALE, "detail": "Link down on every watched interface."}
        return {**row, "state": STALE, "detail": "No watched interface found."}

    def _nightly_rows(self, now: int) -> list[dict]:
        out = []
        for job in self.nightly() or []:
            row = {
                "key": job["key"],
                "label": job["label"],
                "reads_24h": None,
                "error": None,
                "last_ok_ms": job.get("last_ok_ms"),
                "fix": {"action": job["key"], "label": "Run now"},
            }
            last, last_ok = job.get("last_ms"), job.get("last_ok_ms")
            if not job.get("enabled"):
                out.append(
                    {
                        **row,
                        "state": OFF,
                        "detail": "Nightly run is off (Admin settings).",
                        "fix": None,
                    }
                )
            elif last and job.get("last_error"):  # the latest run failed
                out.append(
                    {
                        **row,
                        "state": STALE,
                        "error": job["last_error"],
                        "detail": f"Last run failed ({_ago(now, last)} ago).",
                    }
                )
            elif last_ok and now - last_ok <= NIGHTLY_STALE_MS:
                out.append({**row, "state": OK, "detail": f"Last run {_ago(now, last_ok)} ago."})
            elif now - self.started_ms <= NIGHTLY_STALE_MS:
                out.append({**row, "state": WAITING, "detail": "Runs tonight.", "fix": None})
            else:
                when = f"{_ago(now, last_ok)} ago" if last_ok else "never"
                out.append({**row, "state": STALE, "detail": f"No nightly run since: {when}."})
        return out

    def _weekly_row(self, now: int) -> dict | None:
        if self.weekly is None:
            return None
        last = self.weekly()
        row = {
            "key": "weekly",
            "label": "Weekly report",
            "last_ok_ms": last,
            "reads_24h": None,
            "error": None,
            "fix": {"action": "weekly", "label": "Write it now"},
        }
        if last and now - last <= WEEKLY_STALE_MS:
            return {**row, "state": OK, "detail": f"Last written {_ago(now, last)} ago."}
        if now - self.started_ms <= WEEKLY_STALE_MS:
            return {**row, "state": WAITING, "detail": "Written on Monday morning.", "fix": None}
        return {**row, "state": STALE, "detail": "No report in over a week."}

    def _helper_row(self) -> dict | None:
        if self.helper is None:
            return None
        h = self.helper() or {}
        row = {
            "key": "helper",
            "label": "Update helper",
            "last_ok_ms": h.get("last_seen_ms"),
            "reads_24h": None,
            "error": None,
            "fix": None,
        }
        if not h.get("installed"):
            return {
                **row,
                "state": OFF,
                "detail": "Not installed: updates from the web page are unavailable.",
            }
        if h.get("connected"):
            return {**row, "state": OK, "detail": "Checking in."}
        return {
            **row,
            "state": STALE,
            "detail": "Stopped checking in: is the TrueNAS cron job still there?",
        }

    def rows(self, now_ms: int | None = None) -> list[dict]:
        now = now_ms or int(time.time() * 1000)
        self._sample(now)
        rows = [self._poller_row(p, now) for p in self.pollers]
        rows += [
            r
            for r in (
                self._capture_row(now),
                self._nic_row(now),
                *self._nightly_rows(now),
                self._weekly_row(now),
                self._helper_row(),
            )
            if r is not None
        ]
        return rows

    def view(self, now_ms: int | None = None) -> dict:
        rows = self.rows(now_ms)
        problems = [r for r in rows if r["state"] in PROBLEMS]
        return {"rows": rows, "problems": len(problems)}

    # ── timeline events ──────────────────────────────────────────────────────

    def check(self, now_ms: int | None = None) -> list[dict]:
        """Open an information event for a source stale for 30 min; close it when it reads."""
        now = now_ms or int(time.time() * 1000)
        open_ids = dict(self.kv_get(EVENTS_KEY, None) or {})
        changes = []
        for r in self.rows(now):
            k = r["key"]
            bad = r["state"] in PROBLEMS
            if bad:
                self._stale_since.setdefault(k, now)
            else:
                self._stale_since.pop(k, None)
            if bad and k not in open_ids and now - self._stale_since[k] >= EVENT_AFTER_MS:
                title = f"{r['label']} has stopped working"
                open_ids[k] = self.events.open(
                    INTEGRATION,
                    "info",
                    self._stale_since[k],
                    title,
                    details={
                        "type": "integration_stale",
                        "source": k,
                        "detail": r["detail"],
                        "error": r["error"],
                        "hint": "The System page's integration health card has the fix.",
                    },
                )
                changes.append({"source": k, "action": "opened"})
            elif not bad and k in open_ids:
                self.events.close(open_ids.pop(k), now)
                changes.append({"source": k, "action": "closed"})
        self.kv_set(EVENTS_KEY, open_ids)
        return changes

    # ── background ───────────────────────────────────────────────────────────

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-integration-health")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(TICK_S)
            n += 1
            try:
                if n % 4 == 0:  # events once a minute; reads sampled every tick
                    await asyncio.to_thread(self.check)
                else:
                    await asyncio.to_thread(self._sample, int(time.time() * 1000))
            except Exception:
                log.exception("integration health check failed")


def _ago(now: int, ts: int | None) -> str:
    if not ts:
        return "never"
    s = max(0, (now - ts) // 1000)
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    if s < 2 * 86400:
        return f"{s // 3600} h"
    return f"{s // 86400} days"
