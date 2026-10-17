from netwatch.probes.base import ProbeResult
from netwatch.services.live import LiveState


def _feed(live, oks, start_ms=1_000_000, step_ms=1000):
    for i, ok in enumerate(oks):
        live.record(ProbeResult(1, start_ms + i * step_ms, ok, 5.0 if ok else None))
    return start_ms + (len(oks) - 1) * step_ms


def test_unknown_without_samples():
    live = LiveState()
    live.track(1, 1.0)
    assert live.status(1, 0)["state"] == "unknown"
    assert live.status(42, 0)["state"] == "unknown"


def test_up_degraded_down():
    live = LiveState()
    live.track(1, 1.0)
    last = _feed(live, [True] * 10)
    assert live.status(1, last)["state"] == "up"
    last = _feed(live, [False, True], start_ms=last + 1000)
    assert live.status(1, last)["state"] == "degraded"
    last = _feed(live, [False] * 3, start_ms=last + 1000)
    st = live.status(1, last)
    assert st["state"] == "down" and st["consecutive_failures"] == 3


def test_stale_is_unknown():
    live = LiveState()
    live.track(1, 1.0)
    last = _feed(live, [True])
    assert live.status(1, last + 60_000)["state"] == "unknown"


def test_untracked_results_ignored():
    live = LiveState()
    live.record(ProbeResult(9, 1, True, 1.0))
    assert live.status(9, 1)["state"] == "unknown"
