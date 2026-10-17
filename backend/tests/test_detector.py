from netwatch.probes.base import ProbeResult
from netwatch.services.detector import DEGRADED, DOWN, UNKNOWN, UP, DetectorConfig, OutageDetector


def feed(det, oks, start=0, step=1000, tid=1):
    out = []
    for i, ok in enumerate(oks):
        t = det.observe(
            ProbeResult(tid, start + i * step, ok, 1.0 if ok else None, None if ok else "timeout")
        )
        if t:
            out.append(t)
    return out


def make(**kw):
    d = OutageDetector(DetectorConfig(**kw))
    d.track(1, 1.0)
    return d


def test_untracked_ignored():
    d = OutageDetector()
    assert d.observe(ProbeResult(9, 0, False)) is None
    assert d.state(9) == UNKNOWN


def test_first_success_is_up():
    d = make()
    [t] = feed(d, [True])
    assert (t.prev, t.new) == (UNKNOWN, UP)


def test_single_and_double_loss_do_not_open_outage():
    d = make(degraded_enter_pct=50)
    feed(d, [True] * 5 + [False, True, False, False, True])
    assert d.state(1) == UP


def test_outage_starts_at_first_failure_and_recovers_with_hysteresis():
    d = make(down_after=3, up_after=3)
    feed(d, [True] * 10)
    trans = feed(d, [False, False, False, False], start=10_000)
    assert [(t.prev, t.new) for t in trans] == [(UP, DOWN)]
    assert trans[0].ts_ms == 10_000 and trans[0].last_error == "timeout"
    # Two successes are not enough; a failure in between resets the count.
    assert feed(d, [True, True, False], start=14_000) == []
    trans = feed(d, [True, True, True], start=17_000)
    assert [(t.prev, t.new) for t in trans] == [(DOWN, UP)]
    assert trans[0].ts_ms == 17_000
    assert d.since_ms(1) == 17_000


def test_degraded_enter_and_exit_thresholds():
    d = make(degraded_enter_pct=10, degraded_exit_pct=2)
    feed(d, [True] * 20)
    pattern = ([False] + [True] * 4) * 4  # 20 % loss, never 3 in a row
    trans = feed(d, pattern, start=20_000)
    assert trans and trans[0].new == DEGRADED and trans[0].loss_pct >= 10
    # Loss must fall to <= 2 % over the 60 s window before it clears.
    trans = feed(d, [True] * 60, start=40_000)
    assert [t.new for t in trans] == [UP]


def test_degraded_to_down():
    d = make(degraded_enter_pct=5)
    feed(d, [True] * 10)
    feed(d, [True, True, True, False, True], start=10_000)
    assert d.state(1) == DEGRADED
    [t] = feed(d, [False, False, False], start=15_000)
    assert (t.prev, t.new) == (DEGRADED, DOWN) and t.ts_ms == 15_000


def test_outage_never_flashes_degraded_first():
    d = make(degraded_enter_pct=5)
    feed(d, [True] * 10)
    trans = feed(d, [False] * 5, start=10_000)
    assert [t.new for t in trans] == [DOWN]


def test_recovery_does_not_flag_degraded_from_outage_failures():
    d = make()
    feed(d, [True] * 10 + [False] * 20 + [True] * 3)
    assert d.state(1) == UP


def test_forget():
    d = make()
    feed(d, [True])
    d.forget(1)
    assert d.state(1) == UNKNOWN and d.tracked() == {}
