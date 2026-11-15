"""Who's home: people, the devices that are theirs, and arrivals and departures.

A person is home while any of their devices (including its private-MAC siblings) was seen in the
last ``away_after`` minutes, so a phone that sleeps its Wi-Fi does not flap. An arrival is noted
as soon as a device is seen again after a departure. State is kept in kv, so a restart does not
invent arrivals. Everything stays on the LAN.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, delete, func, insert, or_, select, update

from ..db import devices, events, people, people_devices
from .device_groups import COLOURS
from .events import PRESENCE, EventLog

log = logging.getLogger(__name__)

STATE_KEY = "presence_state"  # {person_id: {"home": bool, "since_ms": int}}
SETTINGS_KEY = "presence_settings"  # {"away_after_min": int}
DEFAULT_AWAY_MIN = 15
AWAY_RANGE = (5, 240)
MAX_PEOPLE = 20
MAX_DEVICES = 8  # per person
TICK_S = 30


class PeopleError(ValueError):
    pass


class PeopleStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(people).order_by(people.c.sort, people.c.name)).all()
            links = conn.execute(select(people_devices)).all()
        by_person: dict[int, list[int]] = {}
        for pid, did in links:
            by_person.setdefault(pid, []).append(did)
        return [{**r._mapping, "device_ids": sorted(by_person.get(r.id, []))} for r in rows]

    def get(self, person_id: int) -> dict | None:
        return next((p for p in self.list() if p["id"] == person_id), None)

    def _check(self, name: str, colour: str, exclude: int | None = None) -> str:
        name = " ".join(name.split())
        if not name or len(name) > 40:
            raise PeopleError("a name of 1 to 40 characters is needed")
        if colour not in COLOURS:
            raise PeopleError("unknown colour")
        with self.engine.connect() as conn:
            q = select(people.c.id).where(func.lower(people.c.name) == name.lower())
            if exclude is not None:
                q = q.where(people.c.id != exclude)
            if conn.execute(q).first():
                raise PeopleError(f"{name} is already on the list")
        return name

    def _primaries(self, conn, device_ids: list[int]) -> list[int]:
        """Private-MAC siblings point at their primary row; store the primary."""
        if len(set(device_ids)) > MAX_DEVICES:
            raise PeopleError(f"at most {MAX_DEVICES} devices per person")
        rows = conn.execute(
            select(devices.c.id, devices.c.merged_into).where(devices.c.id.in_(device_ids))
        ).all()
        if len(rows) != len(set(device_ids)):
            raise PeopleError("unknown device")
        return sorted({r.merged_into or r.id for r in rows})

    def create(self, name: str, colour: str, device_ids: list[int] | None = None) -> dict:
        name = self._check(name, colour)
        with self.engine.begin() as conn:
            if conn.execute(select(func.count()).select_from(people)).scalar() >= MAX_PEOPLE:
                raise PeopleError(f"at most {MAX_PEOPLE} people")
            pid = conn.execute(
                insert(people).values(
                    name=name, colour=colour, sort=0, created_ms=int(time.time() * 1000)
                )
            ).inserted_primary_key[0]
            ids = self._primaries(conn, device_ids or [])
            if ids:
                rows = [{"person_id": pid, "device_id": d} for d in ids]
                conn.execute(insert(people_devices), rows)
        return self.get(pid)  # type: ignore[return-value]

    def update(
        self,
        person_id: int,
        *,
        name: str | None = None,
        colour: str | None = None,
        device_ids: list[int] | None = None,
    ) -> dict:
        cur = self.get(person_id)
        if cur is None:
            raise PeopleError("no such person")
        values = {}
        if name is not None or colour is not None:
            values["name"] = self._check(name or cur["name"], colour or cur["colour"], person_id)
            values["colour"] = colour or cur["colour"]
        with self.engine.begin() as conn:
            if values:
                conn.execute(update(people).where(people.c.id == person_id).values(**values))
            if device_ids is not None:
                ids = self._primaries(conn, device_ids)
                conn.execute(delete(people_devices).where(people_devices.c.person_id == person_id))
                if ids:
                    conn.execute(
                        insert(people_devices),
                        [{"person_id": person_id, "device_id": d} for d in ids],
                    )
        return self.get(person_id)  # type: ignore[return-value]

    def delete(self, person_id: int) -> int:
        """Remove a person and their arrivals and departures; returns how many events went."""
        with self.engine.begin() as conn:
            gone = conn.execute(
                delete(events).where(
                    events.c.kind == PRESENCE,
                    func.json_extract(events.c.details, "$.person_id") == person_id,
                )
            ).rowcount
            conn.execute(delete(people).where(people.c.id == person_id))
        return gone

    def last_seen(self) -> dict[int, int | None]:
        """Per person: the freshest sighting of any of their devices or its private-MAC siblings."""
        out: dict[int, int | None] = {}
        with self.engine.connect() as conn:
            for p in conn.execute(select(people.c.id)).all():
                ids = select(people_devices.c.device_id).where(people_devices.c.person_id == p.id)
                seen = conn.execute(
                    select(func.max(devices.c.last_seen_ms)).where(
                        or_(devices.c.id.in_(ids), devices.c.merged_into.in_(ids))
                    )
                ).scalar()
                out[p.id] = seen
        return out


class PresenceService:
    def __init__(
        self,
        store: PeopleStore,
        events_log: EventLog,
        kv_get: Callable[[str, object], object],
        kv_set: Callable[[str, object], None],
        *,
        tz: Callable[[], str] = lambda: "America/Toronto",
    ) -> None:
        self.people = store
        self.events = events_log
        self.kv_get, self.kv_set = kv_get, kv_set
        self.tz = tz
        self._task: asyncio.Task | None = None

    # ── settings ─────────────────────────────────────────────────────────────

    def settings(self) -> dict:
        s = self.kv_get(SETTINGS_KEY, None) or {}
        away = s.get("away_after_min", DEFAULT_AWAY_MIN) if isinstance(s, dict) else None
        if not isinstance(away, int) or not AWAY_RANGE[0] <= away <= AWAY_RANGE[1]:
            away = DEFAULT_AWAY_MIN
        return {"away_after_min": away}

    def set_settings(self, away_after_min: int) -> dict:
        if not AWAY_RANGE[0] <= away_after_min <= AWAY_RANGE[1]:
            raise PeopleError(f"away after must be {AWAY_RANGE[0]} to {AWAY_RANGE[1]} minutes")
        self.kv_set(SETTINGS_KEY, {"away_after_min": away_after_min})
        return self.settings()

    # ── evaluation ───────────────────────────────────────────────────────────

    def _state(self) -> dict[str, dict]:
        s = self.kv_get(STATE_KEY, None)
        return dict(s) if isinstance(s, dict) else {}

    def tick(self, now_ms: int | None = None) -> list[dict]:
        """Update who is home; returns the arrivals and departures recorded."""
        now = now_ms or int(time.time() * 1000)
        away_ms = self.settings()["away_after_min"] * 60_000
        plist = {p["id"]: p for p in self.people.list()}
        seen = self.people.last_seen()
        old = self._state()
        state: dict[str, dict] = {}
        changes = []
        for pid, p in plist.items():
            last = seen.get(pid)
            prev = old.get(str(pid))
            if last is None:  # no devices, or none seen yet
                state[str(pid)] = prev or {"home": False, "since_ms": now}
                continue
            home = now - last <= away_ms
            if prev is None:  # newly added: start from what is true now, without an event
                state[str(pid)] = {"home": home, "since_ms": last if home else last + away_ms}
                continue
            if home and not prev["home"] and last > prev["since_ms"]:
                state[str(pid)] = {"home": True, "since_ms": last}
                changes.append(self._record(p, "arrived", last))
            elif not home and prev["home"]:
                # Leaving is dated by the last sighting; the gap proves they had gone.
                state[str(pid)] = {"home": False, "since_ms": last}
                changes.append(self._record(p, "left", last))
            else:
                state[str(pid)] = prev
        if state != old:
            self.kv_set(STATE_KEY, state)
        return changes

    def _record(self, person: dict, action: str, ts_ms: int) -> dict:
        at = datetime.fromtimestamp(ts_ms / 1000, ZoneInfo(self.tz())).strftime("%H:%M")
        verb = "arrived home" if action == "arrived" else "left"
        title = f"{person['name']} {verb} at {at}"
        self.events.instant(
            PRESENCE,
            "info",
            ts_ms,
            title,
            details={
                "person_id": person["id"],
                "person": person["name"],
                "colour": person["colour"],
                "action": action,
            },
        )
        return {"person_id": person["id"], "action": action, "ts_ms": ts_ms}

    # ── views ────────────────────────────────────────────────────────────────

    def status(self) -> list[dict]:
        state = self._state()
        seen = self.people.last_seen()
        out = []
        for p in self.people.list():
            st = state.get(str(p["id"]))
            out.append(
                {
                    **p,
                    "home": bool(st and st["home"]) if p["device_ids"] else None,
                    "since_ms": st["since_ms"] if st and p["device_ids"] else None,
                    "last_seen_ms": seen.get(p["id"]),
                }
            )
        return out

    def history(self, person_id: int, days: int = 7, now_ms: int | None = None) -> dict:
        """Arrivals and departures, and home/away spans per local day for the last ``days``."""
        now = now_ms or int(time.time() * 1000)
        zone = ZoneInfo(self.tz())
        today = datetime.fromtimestamp(now / 1000, zone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_dt = today - timedelta(days=days - 1)
        start = int(start_dt.timestamp() * 1000)
        evs = [
            e
            for e in self.events.list(start - 30 * 86_400_000, now, kinds=[PRESENCE], limit=5000)
            if e["details"].get("person_id") == person_id
        ]
        evs.sort(key=lambda e: e["start_ms"])
        # Whether they were home when the window starts: the last event before it.
        before = [e for e in evs if e["start_ms"] < start]
        home = bool(before) and before[-1]["details"].get("action") == "arrived"
        known = bool(before)
        if not known:
            st = self._state().get(str(person_id))
            first = next((e for e in evs if e["start_ms"] >= start), None)
            if first is not None:
                home, known = first["details"].get("action") == "left", True
            elif st is not None:
                home, known = bool(st["home"]), True
        inside = [e for e in evs if e["start_ms"] >= start]
        out_days = []
        for i in range(days):
            d0 = start_dt + timedelta(days=i)
            a = int(d0.timestamp() * 1000)
            b = min(int((d0 + timedelta(days=1)).timestamp() * 1000), now)
            spans, t = [], a
            for e in (x for x in inside if a <= x["start_ms"] < b):
                if t < e["start_ms"]:
                    span = {"start_ms": t, "end_ms": e["start_ms"], "home": home if known else None}
                    spans.append(span)
                home, known, t = e["details"].get("action") == "arrived", True, e["start_ms"]
            if t < b:
                spans.append({"start_ms": t, "end_ms": b, "home": home if known else None})
            out_days.append({"date": d0.date().isoformat(), "start_ms": a, "spans": spans})
        return {
            "events": [
                {"ts_ms": e["start_ms"], "action": e["details"].get("action"), "title": e["title"]}
                for e in inside
            ],
            "days": out_days,
        }

    def week_summary(self, start_ms: int, end_ms: int) -> list[dict]:
        """For the weekly report: arrivals per person (no hours, to keep it light)."""
        evs = self.events.list(start_ms, end_ms, kinds=[PRESENCE], limit=20_000)
        out = []
        for p in self.people.list():
            n = sum(
                1
                for e in evs
                if e["details"].get("person_id") == p["id"]
                and e["details"].get("action") == "arrived"
            )
            out.append({"name": p["name"], "arrivals": n})
        return out

    # ── background ───────────────────────────────────────────────────────────

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="netwatch-presence")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                log.exception("presence check failed")
