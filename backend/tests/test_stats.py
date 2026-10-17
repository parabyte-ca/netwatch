import pytest

from netwatch.services.stats import aggregate, loss_pct, merge, percentile


def test_percentile_interpolates():
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([5.0], 95) == 5.0
    assert percentile([1.0, 2.0, 3.0], 0) == 1.0
    assert percentile([1.0, 2.0, 3.0], 100) == 3.0


def test_aggregate_counts_loss_and_jitter():
    b = aggregate([10.0, None, 12.0, 11.0, None])
    assert b["count"] == 5
    assert b["lost"] == 2
    assert b["min"] == 10.0
    assert b["max"] == 12.0
    assert b["avg"] == pytest.approx(11.0)
    assert b["median"] == 11.0
    # consecutive successful replies: |12-10| + |11-12| over 2 pairs
    assert b["jitter"] == pytest.approx(1.5)
    assert loss_pct(b) == 40.0


def test_aggregate_all_lost():
    b = aggregate([None, None])
    assert b["count"] == 2 and b["lost"] == 2
    assert b["min"] is None and b["median"] is None
    assert loss_pct(b) == 100.0


def test_aggregate_empty():
    b = aggregate([])
    assert b["count"] == 0
    assert loss_pct(b) is None


def test_merge_exact_fields_and_weighted_percentiles():
    a = aggregate([10.0, 10.0, 10.0, None])  # 3 ok
    b = aggregate([20.0])  # 1 ok
    m = merge([a, b])
    assert m["count"] == 5 and m["lost"] == 1
    assert m["min"] == 10.0 and m["max"] == 20.0
    assert m["avg"] == pytest.approx(12.5)  # (3*10 + 1*20) / 4
    assert m["median"] == pytest.approx(12.5)


def test_merge_skips_fully_lost_buckets():
    m = merge([aggregate([None, None]), aggregate([5.0])])
    assert m["count"] == 3 and m["lost"] == 2
    assert m["min"] == 5.0


def test_merge_empty():
    m = merge([])
    assert m["count"] == 0 and m["min"] is None
