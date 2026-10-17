from netwatch.probes.base import ProbeResult
from netwatch.services.series import build_series
from netwatch.services.storage import WATERMARK_KEY

from .conftest import make_target

NOW = 1_760_003_580  # minute aligned


def test_short_range_uses_raw_buckets(store):
    t = make_target(store, interval_s=1.0)
    start = NOW - 600
    store.insert_samples(
        [
            ProbeResult(t["id"], (start + i) * 1000, i % 10 != 0, None if i % 10 == 0 else 5.0)
            for i in range(600)
        ]
    )
    out = build_series(store, t, start, NOW, now=NOW, raw_retention_days=7, points=60)
    assert out["source"] == "raw"
    assert out["step"] == 10
    s = out["series"]
    assert len(s["ts"]) == 60
    assert s["count"][0] == 10 and s["lost"][0] == 1 and s["loss_pct"][0] == 10.0
    assert out["summary"]["count"] == 600 and out["summary"]["loss_pct"] == 10.0


def test_long_range_uses_rollups(store):
    t = make_target(store, interval_s=1.0)
    start = NOW - 86400
    store.insert_samples(
        [ProbeResult(t["id"], (start + i * 60) * 1000, True, 5.0) for i in range(1440)]
    )
    store.kv_set(WATERMARK_KEY, start)
    store.rollup_pending(NOW + 60, raw_retention_days=7)
    out = build_series(store, t, start, NOW, now=NOW, raw_retention_days=7, points=720)
    assert out["source"] == "rollup"
    assert out["step"] == 120
    assert len(out["series"]["ts"]) == 720
    assert out["summary"]["count"] == 1440 and out["summary"]["lost"] == 0


def test_range_beyond_raw_retention_uses_rollups(store):
    t = make_target(store)
    start = NOW - 8 * 86400
    out = build_series(store, t, start, start + 3600, now=NOW, raw_retention_days=7)
    assert out["source"] == "rollup"
    assert len(out["series"]["ts"]) == 60  # empty buckets are emitted as gaps
    assert set(out["series"]["count"]) == {0}
    assert set(out["series"]["median"]) == {None}
    assert out["summary"]["count"] == 0 and out["summary"]["loss_pct"] is None


def test_missing_buckets_are_gaps(store):
    t = make_target(store, interval_s=1.0)
    start = NOW - 600
    store.insert_samples([ProbeResult(t["id"], (start + i) * 1000, True, 5.0) for i in range(60)])
    out = build_series(store, t, start, NOW, now=NOW, raw_retention_days=7, points=60)
    s = out["series"]
    assert len(s["ts"]) == 60 and s["ts"][1] - s["ts"][0] == 10
    assert s["median"][:6] == [5.0] * 6
    assert s["median"][6:] == [None] * 54 and s["count"][6:] == [0] * 54
