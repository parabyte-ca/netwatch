"""Schedules for a group or a device: time windows when AdGuard Home settings get stricter.

A schedule has days, a start and an end time (a window may cross midnight, e.g. 21:00 to 07:00)
and effects for while it is active: internet off, YouTube off, extra apps blocked, strict
filtering. Times are local (``settings.tz``), so daylight saving changes are handled by the time
zone. Exceptions come from NetWatch: "+15 min" and "+1 hour" (the schedule is off until then),
"until it next starts" (off until the current window ends) and "skip today" (the current or next
window is skipped). A manual window turns the internet off now for a set time.

This module only stores and evaluates; ProfileSync applies the result to AdGuard Home.
"""

from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .actions import ActionError

SCHEDULES_KEY = "adguard_schedules"  # {"schedules": [...], "exceptions": {id: ms}, "manual": [...]}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_SCHEDULES = 50
MAX_MANUAL_MIN = 12 * 60
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_SERVICE_ID = re.compile(r"^[a-z0-9_]{1,64}$")

PRESETS: tuple[dict, ...] = (
    {"name": "School nights", "days": ["sun", "mon", "tue", "wed", "thu"], "start": "21:00",
     "end": "07:00", "effects": {"internet_off": True}},
    {"name": "Homework", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "16:00",
     "end": "18:00", "effects": {"youtube_off": True, "services": []}},
    {"name": "Bedtime", "days": list(DAYS), "start": "22:00", "end": "06:30",
     "effects": {"internet_off": True}},
)  # fmt: skip


def _effects(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    svc = sorted({str(s) for s in raw.get("services") or [] if _SERVICE_ID.match(str(s))})
    return {
        "internet_off": bool(raw.get("internet_off")),
        "youtube_off": bool(raw.get("youtube_off")),
        "strict": bool(raw.get("strict")),
        "services": svc[:30],
    }


def describe_effects(e: dict) -> str:
    """ "internet off" or "YouTube off, blocks TikTok" for events and the UI."""
    e = _effects(e)
    if e["internet_off"]:
        return "internet off"
    parts = []
    if e["strict"]:
        parts.append("strict filtering")
    if e["youtube_off"]:
        parts.append("YouTube off")
    if e["services"]:
        parts.append("blocks " + ", ".join(e["services"]))
    return ", ".join(parts) or "no change"


def check_schedule(raw: dict) -> dict:
    """A schedule sent by the UI, validated and normalised (no id, times, or exceptions)."""
    if not isinstance(raw, dict):
        raise ActionError("schedule is missing")
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()[:40]
    if not name:
        raise ActionError("give the schedule a name")
    target = raw.get("target") or {}
    if target.get("kind") not in ("group", "device") or not isinstance(target.get("id"), int):
        raise ActionError("a schedule belongs to a group or a device")
    days = [d for d in DAYS if d in set(raw.get("days") or [])]
    if not days:
        raise ActionError("choose at least one day")
    start, end = str(raw.get("start") or ""), str(raw.get("end") or "")
    if not _HHMM.match(start) or not _HHMM.match(end):
        raise ActionError("times are HH:MM, such as 21:00")
    if start == end:
        raise ActionError("the start and end times must differ")
    effects = _effects(raw.get("effects"))
    if describe_effects(effects) == "no change":
        raise ActionError(
            "choose what the schedule does: internet off, YouTube off, apps or strict"
        )
    return {
        "name": name,
        "target": {"kind": target["kind"], "id": int(target["id"])},
        "days": days,
        "start": start,
        "end": end,
        "effects": effects,
        "enabled": bool(raw.get("enabled", True)),
    }


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _at(day: datetime, hhmm: str, tz: ZoneInfo) -> datetime:
    """That local date at HH:MM, as an aware datetime (a gap at a DST change moves forward)."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
    # Round-trip through UTC: a time that does not exist (spring forward) becomes a real one.
    return local.astimezone(ZoneInfo("UTC")).astimezone(tz)


def window(s: dict, now_ms: int, tz: ZoneInfo) -> tuple[int, int] | None:
    """The window (start_ms, end_ms) of schedule ``s`` that contains now, or None."""
    now = datetime.fromtimestamp(now_ms / 1000, tz)
    cross = _minutes(s["end"]) <= _minutes(s["start"])
    for back in (0, 1):  # a window that started yesterday may still be open
        day = (now - timedelta(days=back)).date()
        if DAYS[day.weekday()] not in s["days"]:
            continue
        d = datetime(day.year, day.month, day.day, tzinfo=tz)
        start = _at(d, s["start"], tz)
        end = _at(d + timedelta(days=1) if cross else d, s["end"], tz)
        if start <= now < end:
            return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    return None


def next_window(s: dict, now_ms: int, tz: ZoneInfo) -> tuple[int, int] | None:
    """The window that is open now, or else the next one within a week."""
    w = window(s, now_ms, tz)
    if w:
        return w
    now = datetime.fromtimestamp(now_ms / 1000, tz)
    cross = _minutes(s["end"]) <= _minutes(s["start"])
    for ahead in range(0, 8):
        day = (now + timedelta(days=ahead)).date()
        if DAYS[day.weekday()] not in s["days"]:
            continue
        d = datetime(day.year, day.month, day.day, tzinfo=tz)
        start = _at(d, s["start"], tz)
        if start > now:
            end = _at(d + timedelta(days=1) if cross else d, s["end"], tz)
            return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    return None


class ScheduleStore:
    def __init__(self, store, tz_name: str) -> None:
        self.store = store
        self.tz_name = tz_name

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.tz_name)
        except Exception:  # an unknown zone name: fall back rather than stop schedules
            return ZoneInfo("America/Toronto")

    # ── storage ──────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        d = self.store.kv_get(SCHEDULES_KEY, None) or {}
        return {
            "schedules": [x for x in d.get("schedules") or [] if isinstance(x, dict)],
            "exceptions": {str(k): int(v) for k, v in (d.get("exceptions") or {}).items()},
            "manual": [x for x in d.get("manual") or [] if isinstance(x, dict)],
        }

    def _save(self, d: dict) -> None:
        self.store.kv_set(SCHEDULES_KEY, d)

    def list(self) -> list[dict]:
        return self._load()["schedules"]

    def get(self, sid: str) -> dict | None:
        return next((s for s in self.list() if s["id"] == sid), None)

    def add(self, raw: dict, by: str) -> dict:
        s = check_schedule(raw)
        d = self._load()
        if len(d["schedules"]) >= MAX_SCHEDULES:
            raise ActionError(f"at most {MAX_SCHEDULES} schedules")
        s = {"id": secrets.token_hex(4), **s, "created_ms": int(time.time() * 1000), "by": by}
        d["schedules"].append(s)
        self._save(d)
        return s

    def update(self, sid: str, raw: dict) -> dict:
        d = self._load()
        cur = next((s for s in d["schedules"] if s["id"] == sid), None)
        if cur is None:
            raise ActionError("no such schedule")
        cur.update(check_schedule({**cur, **raw}))
        d["exceptions"].pop(sid, None)  # a changed schedule starts fresh
        self._save(d)
        return cur

    def delete(self, sid: str) -> dict:
        d = self._load()
        cur = next((s for s in d["schedules"] if s["id"] == sid), None)
        if cur is None:
            raise ActionError("no such schedule")
        d["schedules"] = [s for s in d["schedules"] if s["id"] != sid]
        d["exceptions"].pop(sid, None)
        self._save(d)
        return cur

    def copy_device(
        self, source: int, dests: list[int], mode: str, by: str, save: bool = True
    ) -> dict:
        """Copy one device's own schedules to other devices. ``merge`` adds the ones a device
        does not already have, ``replace`` deletes its own first. Temporary "off until" exceptions
        and manual windows are never copied. Nothing is stored unless ``save`` (a preview).
        Returns {dest id: {"added": n, "removed": n}}; raises ActionError past the limit."""
        d = self._load()
        mine = {"kind": "device", "id": source}
        src = [s for s in d["schedules"] if s["target"] == mine]

        def sig(s: dict) -> tuple:
            eff = repr(sorted(s["effects"].items()))
            return (s["name"], tuple(s["days"]), s["start"], s["end"], eff, s.get("enabled", True))

        out: dict[int, dict] = {}
        for dest in dests:
            to = {"kind": "device", "id": dest}
            own = [s for s in d["schedules"] if s["target"] == to]
            if mode == "replace":
                gone = {s["id"] for s in own}
                d["schedules"] = [s for s in d["schedules"] if s["id"] not in gone]
                for sid in gone:
                    d["exceptions"].pop(sid, None)
                own = []
            have = {sig(s) for s in own}
            fresh = [s for s in src if sig(s) not in have]
            now = int(time.time() * 1000)
            for s in fresh:
                d["schedules"].append({**s, "id": secrets.token_hex(4), "target": to,
                                       "created_ms": now, "by": by})  # fmt: skip
            out[dest] = {"added": len(fresh), "removed": len(gone) if mode == "replace" else 0}
        if len(d["schedules"]) > MAX_SCHEDULES:
            raise ActionError(f"at most {MAX_SCHEDULES} schedules")
        if save:
            self._save(d)
        return out

    def drop_target(self, kind: str, tid: int) -> int:
        """Remove the schedules and manual windows of a deleted group or device."""
        d = self._load()
        keep = [s for s in d["schedules"] if s["target"] != {"kind": kind, "id": tid}]
        gone = len(d["schedules"]) - len(keep)
        d["schedules"] = keep
        d["manual"] = [m for m in d["manual"] if m["target"] != {"kind": kind, "id": tid}]
        self._save(d)
        return gone

    # ── exceptions ───────────────────────────────────────────────────────────

    def off_until(self, sid: str, until_ms: int) -> None:
        d = self._load()
        d["exceptions"][sid] = max(int(until_ms), d["exceptions"].get(sid, 0))
        self._save(d)

    def extend(self, sid: str, how: str, now_ms: int | None = None) -> int:
        """+15 min, +1 hour, until the window ends, or skip the current or next window.

        Returns the time until which the schedule is off.
        """
        now = now_ms or int(time.time() * 1000)
        s = self.get(sid)
        if s is None:
            raise ActionError("no such schedule")
        cur = self._load()["exceptions"].get(sid, 0)
        if how in ("15", "60"):
            if window(s, now, self.tz) is None:
                raise ActionError("the schedule is not active now")
            until = max(now, cur) + int(how) * 60_000
        elif how == "until_next":
            w = window(s, now, self.tz)
            if w is None:
                raise ActionError("the schedule is not active now")
            until = w[1]
        elif how == "skip":
            w = next_window(s, now, self.tz)
            if w is None:
                raise ActionError("the schedule has no window this week")
            until = w[1]
        else:
            raise ActionError("choose 15, 60, until_next or skip")
        self.off_until(sid, until)
        return until

    def clear_exception(self, sid: str) -> None:
        d = self._load()
        d["exceptions"].pop(sid, None)
        self._save(d)

    # ── manual windows ───────────────────────────────────────────────────────

    def manual_on(self, target: dict, minutes: int, by: str, now_ms: int | None = None) -> dict:
        if target.get("kind") not in ("group", "device") or not isinstance(target.get("id"), int):
            raise ActionError("choose a group or a device")
        if not 1 <= int(minutes) <= MAX_MANUAL_MIN:
            raise ActionError("between 1 minute and 12 hours")
        now = now_ms or int(time.time() * 1000)
        d = self._load()
        d["manual"] = [m for m in d["manual"] if m["until_ms"] > now and m["target"] != target]
        m = {"id": "m-" + secrets.token_hex(3), "target": dict(target), "start_ms": now,
             "until_ms": now + int(minutes) * 60_000, "effects": _effects({"internet_off": True}),
             "name": "Internet off now", "by": by}  # fmt: skip
        d["manual"].append(m)
        self._save(d)
        return m

    def manual_off(self, mid: str) -> dict:
        d = self._load()
        m = next((x for x in d["manual"] if x["id"] == mid), None)
        if m is None:
            raise ActionError("that is no longer on")
        d["manual"] = [x for x in d["manual"] if x["id"] != mid]
        self._save(d)
        return m

    # ── what is in force ─────────────────────────────────────────────────────

    def active(self, now_ms: int | None = None) -> list[dict]:
        """Schedules and manual windows in force now: [{id, name, target, effects, start_ms,
        end_ms, manual}]. Tidies expired exceptions and manual windows."""
        now = now_ms or int(time.time() * 1000)
        d = self._load()
        out = []
        for s in d["schedules"]:
            if not s.get("enabled", True):
                continue
            w = window(s, now, self.tz)
            if w is None or d["exceptions"].get(s["id"], 0) > now:
                continue
            out.append({"id": s["id"], "name": s["name"], "target": s["target"],
                        "effects": s["effects"], "start_ms": w[0], "end_ms": w[1],
                        "manual": False})  # fmt: skip
        for m in d["manual"]:
            if m["start_ms"] <= now < m["until_ms"]:
                out.append({"id": m["id"], "name": m["name"], "target": m["target"],
                            "effects": m["effects"], "start_ms": m["start_ms"],
                            "end_ms": m["until_ms"], "manual": True})  # fmt: skip
        tidy_exc = {k: v for k, v in d["exceptions"].items() if v > now}
        tidy_man = [m for m in d["manual"] if m["until_ms"] > now]
        if tidy_exc != d["exceptions"] or len(tidy_man) != len(d["manual"]):
            d["exceptions"], d["manual"] = tidy_exc, tidy_man
            self._save(d)
        return out

    def state(self, now_ms: int | None = None) -> dict:
        now = now_ms or int(time.time() * 1000)
        d = self._load()
        rows = []
        for s in d["schedules"]:
            w = next_window(s, now, self.tz)
            rows.append({**s, "next": {"start_ms": w[0], "end_ms": w[1]} if w else None,
                         "off_until_ms": d["exceptions"].get(s["id"]) if d["exceptions"].get(
                             s["id"], 0) > now else None,
                         "summary": describe_effects(s["effects"])})  # fmt: skip
        return {"schedules": rows, "active": self.active(now), "presets": list(PRESETS),
                "tz": self.tz_name}  # fmt: skip


def overlay(settings: dict, active_effects: list[dict]) -> tuple[dict, bool]:
    """Settings with the active schedules' effects applied, and whether the internet is off."""
    s = {**settings, "services": list(settings.get("services") or [])}
    off = False
    for e in active_effects:
        e = _effects(e)
        off = off or e["internet_off"]
        if e["strict"]:
            s["filtering"] = "strict"
        if e["youtube_off"]:
            s["youtube"] = "blocked"
        s["services"] = sorted(set(s["services"]) | set(e["services"]))
    return s, off
