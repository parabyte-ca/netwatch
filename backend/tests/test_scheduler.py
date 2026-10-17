import asyncio

from netwatch.services.scheduler import Monitor

from .conftest import FakeProber, make_target


async def _wait_for(cond, limit_s=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + limit_s
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def test_monitor_probes_flushes_and_reloads(store, settings):
    prober = FakeProber()
    a = make_target(store, name="a", host="192.0.2.1", interval_s=1.0)
    make_target(store, name="off", host="192.0.2.9", enabled=False)
    m = Monitor(store, settings, prober=prober)
    await m.start()
    try:
        assert m.running and m.active_targets == 1
        # The background flush loop persists samples; wait for it rather than racing it.
        await _wait_for(lambda: len(store.raw_samples(a["id"], 0, 2**62)) >= 1)

        b = make_target(store, name="b", host="192.0.2.2", interval_s=1.0)
        await m.reload()
        assert m.active_targets == 2
        await _wait_for(lambda: b["id"] in prober.calls)

        store.update_target(a["id"], {"enabled": False})
        await m.reload()
        assert m.active_targets == 1
        assert m.live.status(a["id"], 0)["state"] == "unknown"
    finally:
        await m.stop()
    assert not m.running


async def test_flush_failure_is_retried(store, settings, monkeypatch):
    t = make_target(store)
    m = Monitor(store, settings, prober=FakeProber())
    from netwatch.probes.base import ProbeResult

    m._record(ProbeResult(t["id"], 1000, True, 1.0))

    def broken(_rows):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "insert_samples", broken)
    assert await m.flush() == 0
    assert m.pending_samples == 1 and "disk full" in m.last_flush_error
    monkeypatch.undo()
    assert await m.flush() == 1
    assert m.last_flush_error is None and m.pending_samples == 0
