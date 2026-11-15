from __future__ import annotations

import asyncio
import time

import pytest

from netwatch.services.bufferbloat import BufferbloatStore, BufferbloatTester, HttpLoad
from netwatch.services.events import SPEED, EventLog
from netwatch.services.health import compute, render_week
from netwatch.services.speed_plan import SpeedPlan, point

DAY = 86_400_000
T0 = 1_763_200_000_000


@pytest.fixture
def env(store):
    kv: dict = {}
    plan = {"plan_down_mbps": 1500, "plan_up_mbps": 940, "plan_slow_pct": 70}
    bb = BufferbloatStore(store.engine)
    events = EventLog(store.engine)
    sp = SpeedPlan(bb.list, events, lambda: plan, lambda k, d=None: kv.get(k, d), kv.__setitem__)
    return sp, bb, events, plan


def add(bb: BufferbloatStore, ts: int, down_mbps: float, up_mbps: float, *, trigger="scheduled",
        context: dict | None = None) -> dict:  # fmt: skip
    details = {"context": context} if context is not None else {}
    return bb.add({"ts_ms": ts, "trigger": trigger, "status": "ok", "grade": "A",
                   "down_bps": int(down_mbps * 1e6), "up_bps": int(up_mbps * 1e6),
                   "details": details})  # fmt: skip


def test_point_uses_the_plan_of_the_day_and_flags_the_host_port():
    row = {"ts_ms": T0, "trigger": "scheduled", "down_bps": 940e6, "up_bps": 900e6,
           "details": {"context": {"plan_down_mbps": 1000, "plan_up_mbps": 1000,
                                   "link_mbps": 1000}}}  # fmt: skip
    p = point(row, {"plan_down_mbps": 1500, "plan_up_mbps": 940})
    assert (p["down_pct"], p["up_pct"]) == (94.0, 90.0)  # the plan when it ran, not today's
    assert p["link_capped"] is True and p["link_mbps"] == 1000
    old = {"ts_ms": T0, "down_bps": 750e6, "up_bps": None, "details": {}}
    q = point(old, {"plan_down_mbps": 1500, "plan_up_mbps": 940})
    assert (q["down_pct"], q["up_pct"], q["link_capped"]) == (50.0, None, False)


def test_view_series_last_and_30_day_median(env):
    sp, bb, _, _ = env
    for i, (d, u) in enumerate([(1400, 900), (1350, 920), (1500, 930)]):
        add(bb, T0 - (3 - i) * DAY, d, u)
    add(bb, T0 - 60 * DAY, 300, 100)  # inside 90 days, outside the 30-day median
    v = sp.view(90, now_ms=T0)
    assert v["set"] is True and len(v["points"]) == 4
    assert v["last"]["down_mbps"] == 1500.0
    assert v["median_30d"] == {"down_mbps": 1400.0, "up_mbps": 920.0, "down_pct": 93.3,
                               "up_pct": 97.9, "tests": 3}  # fmt: skip


def test_three_slow_nights_open_one_event_and_a_good_night_closes_it(env):
    sp, bb, events, _ = env
    add(bb, T0 - 4 * DAY, 1400, 900)
    add(bb, T0 - 3 * DAY, 600, 900)
    assert sp.check(T0 - 3 * DAY + 1) is None
    add(bb, T0 - 2 * DAY, 500, 900)
    add(bb, T0 - 2 * DAY + 3600_000, 100, 50, trigger="manual")  # manual tests don't count
    assert sp.check(T0 - 2 * DAY + 3600_001) is None
    add(bb, T0 - DAY, 1400, 500)  # upload slow this time: still a slow night
    r = sp.check(T0 - DAY + 1)
    assert r["action"] == "opened"
    (ev,) = events.list(0, 10**14, kinds=[SPEED])
    assert ev["title"] == "Internet slower than your plan: 3 nights under 70 %"
    assert ev["severity"] == "info" and ev["open"] and ev["start_ms"] == T0 - 3 * DAY
    add(bb, T0, 300, 100)
    assert sp.check(T0 + 1) is None  # still slow: no second event
    add(bb, T0 + DAY, 1450, 920)
    assert sp.check(T0 + DAY + 1)["action"] == "closed"
    (ev,) = events.list(0, 10**14, kinds=[SPEED])
    assert not ev["open"] and ev["end_ms"] == T0 + DAY


def test_nothing_happens_without_a_plan(env):
    sp, bb, events, plan = env
    plan.update(plan_down_mbps=0, plan_up_mbps=0)
    for i in range(4):
        add(bb, T0 + i * DAY, 10, 1)
    assert sp.check(T0 + 4 * DAY) is None and events.list(0, 10**14, kinds=[SPEED]) == []
    assert sp.period(T0, T0 + 5 * DAY) is None
    assert sp.view(90, now_ms=T0 + 4 * DAY)["set"] is False


def test_link_capped_nights_say_so(env):
    sp, bb, events, _ = env
    ctx = {"plan_down_mbps": 1500, "plan_up_mbps": 940, "link_mbps": 1000}
    for i in range(3):
        add(bb, T0 + i * DAY, 940, 600, context=ctx)
    sp.check(T0 + 2 * DAY + 1)
    (ev,) = events.list(0, 10**14, kinds=[SPEED])
    assert ev["details"]["link_capped"] is True and "port was the limit" in ev["details"]["hint"]


def test_score_part_and_weekly_line(env):
    sp, bb, _, _ = env
    add(bb, T0, 1380, 930)
    add(bb, T0 + DAY, 1500, 900)
    speed = sp.period(T0, T0 + 2 * DAY)
    assert speed["tests"] == 2 and speed["down_pct"] == 96.0
    report = {"summary": {"isp_availability_pct": 100.0, "isp_downtime_ms": 0,
                          "isp_downtime": "0 s", "isp_incidents": 0, "incidents": 0,
                          "longest": "none"},
              "targets": [], "incidents": [], "line": {}, "patterns": [], "speeds": [],
              "period": {"start": "2026-11-09", "end": "2026-11-16"}}  # fmt: skip
    without = compute(report)
    with_speed = compute(report, None, speed)
    assert "speed" not in [c["key"] for c in without["components"]]
    part = next(c for c in with_speed["components"] if c["key"] == "speed")
    assert part["weight"] == 10 and part["detail"].startswith("median 96 % of plan down")
    md = render_week("2026-11-09", with_speed, report, None, [], [], speed)
    assert "- Speed: median 96 % of plan down, 97 % of plan up (2 tests)" in md


class CountingLoad(HttpLoad):
    """Slow first second, then a steady 100 Mb/s: the ramp must not drag the result down."""

    async def __call__(self, direction, seconds, counter=None):
        counter = counter if counter is not None else [0]
        start = last = time.monotonic()
        while (now := time.monotonic()) - start < seconds:
            # Bytes follow real elapsed time, so a busy test machine cannot skew the rate.
            rate = 1e6 if now - start < 1.0 else 12.5e6  # bytes per second
            counter[0] += int(rate * (now - last))
            last = now
            await asyncio.sleep(0.02)
        return counter[0]


async def test_throughput_leaves_out_the_ramp_up(store):
    async def pinger(_):
        return 10.0

    t = BufferbloatTester(BufferbloatStore(store.engine), pinger=pinger, loader=CountingLoad(),
                          idle_s=0.2, load_s=3.0,
                          context=lambda: {"plan_down_mbps": 100, "link_mbps": 1000})  # fmt: skip
    row = await t.run("manual")
    assert row["status"] == "ok"
    assert 90e6 <= row["down_bps"] <= 110e6  # about 100 Mb/s, not the ~70 a ramp-included average
    assert row["details"]["context"] == {"plan_down_mbps": 100, "link_mbps": 1000}


async def test_scheduled_result_runs_the_check(store):
    seen = []

    async def pinger(_):
        return 10.0

    async def loader(direction, seconds):
        await asyncio.sleep(seconds)
        return 50_000_000

    t = BufferbloatTester(BufferbloatStore(store.engine), pinger=pinger, loader=loader,
                          idle_s=0.2, load_s=1.2, on_result=seen.append)  # fmt: skip
    row = await t.run("scheduled")
    assert seen == [row]


def test_speed_plan_api(client):
    st = client.app.state
    r = client.get("/api/speed-plan")
    assert r.status_code == 200 and r.json()["set"] is False
    assert client.get("/api/speed-plan?days=2").status_code == 422
    r = client.put("/api/admin/settings", json={"changes": {"plan_down_mbps": 1500,
                                                            "plan_up_mbps": 940}})  # fmt: skip
    assert r.status_code == 200, r.text
    st.bufferbloat_store.add({"ts_ms": int(time.time() * 1000) - 1000, "trigger": "scheduled",
                              "status": "ok", "grade": "A", "down_bps": int(1200e6),
                              "up_bps": int(900e6), "details": {}})  # fmt: skip
    v = client.get("/api/speed-plan").json()
    assert v["set"] is True and v["last"]["down_pct"] == 80.0
    assert v["plan"]["plan_slow_pct"] == 70
