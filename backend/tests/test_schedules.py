from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from netwatch.services.adguard.actions import ActionError
from netwatch.services.adguard.schedules import (
    ScheduleStore,
    check_schedule,
    describe_effects,
    overlay,
    window,
)

TZ = ZoneInfo("America/Toronto")


def ms(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp() * 1000)


KIDS = {"kind": "group", "id": 3}
SCHOOL = {"name": "School nights", "target": KIDS, "days": ["sun", "mon", "tue", "wed", "thu"],
          "start": "21:00", "end": "07:00", "effects": {"internet_off": True}}  # fmt: skip


@pytest.fixture
def sched(store):
    return ScheduleStore(store, "America/Toronto")


def test_validation_and_wording():
    with pytest.raises(ActionError, match="name"):
        check_schedule({**SCHOOL, "name": " "})
    with pytest.raises(ActionError, match="at least one day"):
        check_schedule({**SCHOOL, "days": ["funday"]})
    with pytest.raises(ActionError, match="HH:MM"):
        check_schedule({**SCHOOL, "start": "9pm"})
    with pytest.raises(ActionError, match="must differ"):
        check_schedule({**SCHOOL, "end": "21:00"})
    with pytest.raises(ActionError, match="choose what"):
        check_schedule({**SCHOOL, "effects": {}})
    with pytest.raises(ActionError, match="group or a device"):
        check_schedule({**SCHOOL, "target": {"kind": "room", "id": 1}})
    s = check_schedule({**SCHOOL, "days": ["thu", "sun", "mon"]})
    assert s["days"] == ["mon", "thu", "sun"]  # kept in week order
    assert describe_effects({"internet_off": True, "youtube_off": True}) == "internet off"
    assert describe_effects({"youtube_off": True, "services": ["tiktok"]}) == (
        "YouTube off, blocks tiktok"
    )


def test_windows_cross_midnight_and_follow_the_days():
    s = check_schedule(SCHOOL)
    # Monday 2026-11-16: 21:00 to Tuesday 07:00
    assert window(s, ms(2026, 11, 16, 20, 59), TZ) is None
    assert window(s, ms(2026, 11, 16, 21, 0), TZ) == (ms(2026, 11, 16, 21), ms(2026, 11, 17, 7))
    assert window(s, ms(2026, 11, 17, 6, 59), TZ) == (ms(2026, 11, 16, 21), ms(2026, 11, 17, 7))
    assert window(s, ms(2026, 11, 17, 7, 0), TZ) is None
    # Friday night is not a school night; Saturday morning is not inside Friday's window
    assert window(s, ms(2026, 11, 20, 22), TZ) is None
    assert window(s, ms(2026, 11, 21, 6), TZ) is None
    # Thursday night runs into Friday morning
    assert window(s, ms(2026, 11, 20, 6), TZ) is not None
    day = check_schedule({**SCHOOL, "start": "16:00", "end": "18:00", "days": ["mon"]})
    assert window(day, ms(2026, 11, 16, 17), TZ) and not window(day, ms(2026, 11, 17, 17), TZ)


def test_daylight_saving_change_is_handled_by_the_time_zone():
    s = check_schedule({**SCHOOL, "days": list(("sat", "sun"))})
    # Clocks go back 2026-11-01 02:00 in Toronto: Saturday's window is 11 hours long.
    start, end = window(s, ms(2026, 11, 1, 3), TZ)
    assert (end - start) == 11 * 3_600_000
    # Clocks go forward 2026-03-08: Saturday's window is 9 hours long.
    start, end = window(s, ms(2026, 3, 8, 3), TZ)
    assert (end - start) == 9 * 3_600_000


def test_exceptions_extend_until_next_and_skip(sched):
    s = sched.add(SCHOOL, "admin")
    now = ms(2026, 11, 16, 21, 30)
    assert [a["id"] for a in sched.active(now)] == [s["id"]]
    until = sched.extend(s["id"], "15", now)
    assert until == now + 15 * 60_000 and sched.active(now) == []
    assert sched.extend(s["id"], "15", now) == now + 30 * 60_000  # adds up
    assert sched.active(now + 31 * 60_000)[0]["id"] == s["id"]  # back on afterwards
    assert sched.extend(s["id"], "until_next", now) == ms(2026, 11, 17, 7)
    assert sched.active(ms(2026, 11, 17, 6, 59)) == []
    assert sched.active(ms(2026, 11, 17, 21, 1))  # the next night is not affected
    with pytest.raises(ActionError, match="not active now"):
        sched.extend(s["id"], "15", ms(2026, 11, 17, 12))
    # Skip today, asked in the afternoon: tonight's window is skipped
    assert sched.extend(s["id"], "skip", ms(2026, 11, 18, 15)) == ms(2026, 11, 19, 7)
    assert sched.active(ms(2026, 11, 18, 23)) == []


def test_manual_window_and_disable(sched):
    now = ms(2026, 11, 18, 15)
    m = sched.manual_on(KIDS, 30, "admin", now)
    act = sched.active(now + 60_000)
    assert (
        act[0]["manual"]
        and act[0]["effects"]["internet_off"]
        and act[0]["end_ms"] == now + 1_800_000
    )
    assert sched.active(now + 1_800_000) == []
    with pytest.raises(ActionError):
        sched.manual_on(KIDS, 0, "admin", now)
    m = sched.manual_on(KIDS, 60, "admin", now)
    sched.manual_off(m["id"])
    assert sched.active(now + 60_000) == []
    s = sched.add({**SCHOOL, "enabled": False}, "admin")
    assert sched.active(ms(2026, 11, 16, 22)) == []
    sched.update(s["id"], {"enabled": True})
    assert sched.active(ms(2026, 11, 16, 22))
    assert sched.drop_target("group", 3) == 1 and sched.list() == []


def test_overlay_merges_effects():
    base = {"filtering": "default", "youtube": "default", "services": ["tiktok"]}
    s, off = overlay(base, [{"youtube_off": True, "services": ["roblox"]}, {"strict": True}])
    assert s == {"filtering": "strict", "youtube": "blocked", "services": ["roblox", "tiktok"]}
    assert off is False
    assert overlay(base, [{"internet_off": True}])[1] is True
    assert base["services"] == ["tiktok"]  # not changed in place
