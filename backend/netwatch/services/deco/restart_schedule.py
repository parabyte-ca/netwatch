"""The daily Deco restart: when it is due, and what a stored schedule may hold.

Pure functions, so the poll loop and the tests agree. Local times follow the NetWatch time zone
(``TZ``), so 06:50 stays 06:50 across daylight saving changes.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

GRACE_MS = 30 * 60_000  # a run missed by more than this is skipped, never done mid-morning
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DEFAULT = {"enabled": False, "time": "06:50", "days": [0, 1, 2, 3, 4, 5, 6]}


class ScheduleError(ValueError):
    pass


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # an unknown zone name: fall back rather than stop the schedule
        return ZoneInfo("America/Toronto")


def clean(enabled: object, hhmm: object, days: object) -> dict:
    """Validated schedule values (``days`` are weekdays, Monday = 0)."""
    if not isinstance(hhmm, str) or not _HHMM.match(hhmm):
        raise ScheduleError("time must be HH:MM, 24-hour")
    if (
        not isinstance(days, list)
        or not days
        or any(not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days)
    ):
        raise ScheduleError("pick at least one weekday")
    return {"enabled": bool(enabled), "time": hhmm, "days": sorted(set(days))}


def start_ms(day: datetime, hhmm: str, tz: ZoneInfo) -> int:
    """The moment ``hhmm`` local time on ``day``'s date."""
    h, m = (int(x) for x in hhmm.split(":"))
    return int(datetime.combine(day.date(), time(h, m), tzinfo=tz).timestamp() * 1000)


def due_start(sched: dict, now_ms: int, tz: ZoneInfo) -> int | None:
    """Today's run time if ``now`` is inside its grace window on an enabled day, else None."""
    local = datetime.fromtimestamp(now_ms / 1000, tz)
    if local.weekday() not in sched["days"]:
        return None
    s = start_ms(local, sched["time"], tz)
    return s if s <= now_ms < s + GRACE_MS else None


def missed_start(sched: dict, now_ms: int, tz: ZoneInfo) -> int | None:
    """Today's run time if it is already past its grace window (it should have run), else None."""
    local = datetime.fromtimestamp(now_ms / 1000, tz)
    if local.weekday() not in sched["days"]:
        return None
    s = start_ms(local, sched["time"], tz)
    return s if now_ms >= s + GRACE_MS else None


def next_run(sched: dict, now_ms: int, tz: ZoneInfo) -> int | None:
    """The next run time after ``now`` (within the next 8 days), for display."""
    if not sched["days"]:
        return None
    local = datetime.fromtimestamp(now_ms / 1000, tz)
    for i in range(8):
        day = local + timedelta(days=i)
        if day.weekday() not in sched["days"]:
            continue
        s = start_ms(day, sched["time"], tz)
        if s > now_ms:
            return s
    return None
