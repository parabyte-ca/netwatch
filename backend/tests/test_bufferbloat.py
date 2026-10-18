from __future__ import annotations

import asyncio

import pytest

from netwatch.services.bufferbloat import BufferbloatStore, BufferbloatTester, grade
from netwatch.services.detector import DEGRADED, Transition
from netwatch.services.events import LOSS, EventLog, IncidentRecorder


def test_grades():
    assert [grade(x) for x in (2, 25, 50, 150, 300, 900)] == ["A+", "A", "B", "C", "D", "F"]
    assert grade(None) is None


def make_tester(store, phase_ms: dict, quiet=None):
    state = {"phase": "idle"}

    async def pinger(_host):
        await asyncio.sleep(0)
        return phase_ms[state["phase"]]

    async def loader(direction, seconds):
        state["phase"] = direction
        await asyncio.sleep(seconds)
        state["phase"] = "idle"
        return 50_000_000 if direction == "download" else 5_000_000

    return BufferbloatTester(BufferbloatStore(store.engine), pinger=pinger, loader=loader,
                             quiet=quiet, idle_s=0.3, load_s=1.3)  # fmt: skip


async def test_run_grades_the_worse_direction(store):
    quiet = []
    t = make_tester(store, {"idle": 10.0, "download": 22.0, "upload": 95.0}, quiet.append)
    r = await t.run("manual")
    assert r["status"] == "ok" and r["grade"] == "C"  # upload adds 85 ms
    assert r["idle_ms"] == 10.0 and r["down_ms"] == 22.0 and r["up_ms"] == 95.0
    assert r["down_bps"] > 1e8 and r["details"]["added_ms"] == 85.0
    assert quiet and quiet[0] > r["ts_ms"]
    assert t.store.list()[0]["grade"] == "C"


async def test_no_answer_and_concurrency(store):
    t = make_tester(store, {"idle": None, "download": None, "upload": None})
    first = asyncio.create_task(t.run())
    await asyncio.sleep(0.05)
    with pytest.raises(RuntimeError):
        await t.run()
    r = await first
    assert r["status"] == "error" and "did not answer" in r["error"]


async def test_load_failure_is_recorded(store):
    t = make_tester(store, {"idle": 5.0, "download": 5.0, "upload": 5.0})

    async def broken(direction, seconds):
        raise OSError("no route to speed.cloudflare.com")

    t.loader = broken
    r = await t.run()
    assert r["status"] == "error" and "no route" in r["error"]

    async def nothing(direction, seconds):  # e.g. a proxy that silently drops the streams
        await asyncio.sleep(seconds)
        return 0

    t.loader = nothing
    r = await t.run()
    assert r["status"] == "error" and "could not load" in r["error"] and r.get("grade") is None


async def test_recorder_ignores_failures_during_a_load_test(store):
    from netwatch.probes import TargetSpec

    from .conftest import make_target

    tid = make_target(store, name="Cloudflare", host="1.1.1.1")["id"]
    ev = EventLog(store.engine)
    rec = IncidentRecorder(ev)
    rec.start()
    spec = TargetSpec(id=tid, name="Cloudflare", kind="icmp", host="1.1.1.1", interval_s=1,
                      timeout_s=1, group="public")  # fmt: skip
    rec.quiet_until_ms = 2_000
    rec.submit(Transition(tid, "up", DEGRADED, 1_500, 40.0, None), spec)
    rec.submit(Transition(tid, DEGRADED, "up", 1_900, None, None), spec)
    rec.submit(Transition(tid, "up", DEGRADED, 5_000, 40.0, None), spec)
    await rec.drain()
    await rec.stop()
    opened = ev.list(0, 10_000, kinds=[LOSS])
    assert [e["start_ms"] for e in opened] == [5_000]


def test_api(client):
    st = client.app.state
    st.bufferbloat.pinger = lambda h: _const(8.0)
    st.bufferbloat.loader = _fake_load
    st.bufferbloat.idle_s, st.bufferbloat.load_s = 0.2, 1.2
    assert client.post("/api/bufferbloat/run").status_code == 202
    assert client.post("/api/bufferbloat/run").status_code == 409
    import time

    for _ in range(50):
        body = client.get("/api/bufferbloat").json()
        if body["tests"]:
            break
        time.sleep(0.1)
    assert body["tests"][0]["grade"] == "A+" and body["nightly"] is True


async def _const(v):
    return v


async def _fake_load(direction, seconds):
    await asyncio.sleep(seconds)
    return 10_000_000
