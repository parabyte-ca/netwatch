from netwatch.probes.base import TargetSpec
from netwatch.services.detector import DEGRADED, DOWN, UNKNOWN, UP, Transition
from netwatch.services.events import (
    LOSS,
    MONITOR,
    OUTAGE,
    EventLog,
    IncidentRecorder,
    startup_events,
)

from .conftest import make_target


def spec(tid):
    return TargetSpec(
        id=tid,
        name="Gateway",
        kind="icmp",
        host="192.168.2.1",
        interval_s=1,
        timeout_s=1,
        group="gateway",
    )


def test_event_log_lifecycle(store):
    log = EventLog(store.engine)
    t = make_target(store)
    a = log.open(OUTAGE, "critical", 1000, "down", target_id=t["id"], details={"x": 1})
    b = log.instant(MONITOR, "info", 5000, "started")
    assert log.get(a)["open"] is True and log.get(b)["duration_ms"] == 0
    log.close(a, 4000, {"y": 2})
    ev = log.get(a)
    assert ev["end_ms"] == 4000 and ev["duration_ms"] == 3000 and ev["details"] == {"x": 1, "y": 2}
    log.close(a, 9000)  # already closed: unchanged
    assert log.get(a)["end_ms"] == 4000
    assert [e["id"] for e in log.list(0, 10_000)] == [b, a]
    assert [e["id"] for e in log.list(4500, 10_000)] == [b]
    assert [e["id"] for e in log.list(0, 10_000, kinds=[OUTAGE])] == [a]
    assert [e["id"] for e in log.list(0, 10_000, target_id=t["id"])] == [a]
    assert log.purge(4500) == 1 and log.get(a) is None


def test_open_events_overlap_and_interrupted(store):
    log = EventLog(store.engine)
    a = log.open(OUTAGE, "critical", 1000, "down")
    assert [e["id"] for e in log.list(50_000, 60_000)] == [
        a
    ]  # still open overlaps any later window
    assert [e["id"] for e in log.list(0, 99_999, open_only=True)] == [a]
    assert log.close_interrupted(7000) == 1
    assert log.get(a)["details"]["interrupted"] is True and log.get(a)["end_ms"] == 7000


def test_startup_events(store):
    log = EventLog(store.engine)
    log.open(OUTAGE, "critical", 1000, "down")
    assert startup_events(log) == 1
    started = log.list(0, 2**62, kinds=[MONITOR])
    assert started[0]["title"] == "NetWatch started"


async def test_recorder_opens_and_closes(store):
    log = EventLog(store.engine)
    t = make_target(store)
    hooked = []

    async def hook(s, eid):
        hooked.append((s.id, eid))

    rec = IncidentRecorder(log, on_outage=hook)
    rec.start()
    s = spec(t["id"])
    rec.submit(Transition(t["id"], UNKNOWN, UP, 0, None, None), s)
    rec.submit(Transition(t["id"], UP, DEGRADED, 1000, 12.5, None), s)
    rec.submit(Transition(t["id"], DEGRADED, DOWN, 2000, 50.0, "timeout"), s)
    await rec.drain()
    eid = rec.open_event_id(t["id"])
    assert hooked == [(t["id"], eid)]
    loss = log.list(0, 2**62, kinds=[LOSS])[0]
    assert loss["end_ms"] == 2000 and loss["details"]["loss_pct"] == 12.5
    rec.submit(Transition(t["id"], DOWN, UP, 9000, None, None), s)
    await rec.drain()
    out = log.get(eid)
    assert out["start_ms"] == 2000 and out["end_ms"] == 9000
    assert out["details"]["target_name"] == "Gateway" and out["details"]["last_error"] == "timeout"
    await rec.stop()


async def test_recorder_target_removed_closes(store):
    log = EventLog(store.engine)
    t = make_target(store)
    rec = IncidentRecorder(log)
    rec.start()
    rec.submit(Transition(t["id"], UP, DOWN, 1000, None, "timeout"), spec(t["id"]))
    rec.target_removed(t["id"], 5000)
    await rec.drain()
    ev = log.list(0, 2**62, kinds=[OUTAGE])[0]
    assert ev["end_ms"] == 5000 and ev["details"]["monitoring_stopped"] is True
    await rec.stop()
    assert not rec.running
