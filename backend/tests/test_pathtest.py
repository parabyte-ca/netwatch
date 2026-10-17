import json

import pytest

from netwatch.probes.base import TargetSpec
from netwatch.services.pathtest import PathStore, PathTester, first_persistent_loss, parse_mtr_json

from .conftest import make_target

MTR = {
    "report": {
        "mtr": {"src": "truenas", "dst": "1.1.1.1", "tests": 10},
        "hubs": [
            {
                "count": 1,
                "host": "192.168.2.1",
                "Loss%": 0.0,
                "Snt": 10,
                "Last": 0.5,
                "Avg": 0.6,
                "Best": 0.4,
                "Wrst": 0.9,
                "StDev": 0.1,
            },
            {
                "count": "2",
                "host": "???",
                "Loss%": 100.0,
                "Snt": 10,
                "Last": 0.0,
                "Avg": 0.0,
                "Best": 0.0,
                "Wrst": 0.0,
                "StDev": 0.0,
            },
            {
                "count": 3,
                "host": "10.11.12.13",
                "Loss%": 30.0,
                "Snt": 10,
                "Last": 5,
                "Avg": 6,
                "Best": 4,
                "Wrst": 9,
                "StDev": 1,
            },
            {
                "count": 4,
                "host": "1.1.1.1",
                "Loss%": 20.0,
                "Snt": 10,
                "Last": 7,
                "Avg": 7.5,
                "Best": 6,
                "Wrst": 12,
                "StDev": 1.5,
            },
        ],
    }
}


def hops(*losses):
    """None = a hop that never answers (shown by mtr as ???, 100 % loss)."""
    return [
        {
            "hop": i + 1,
            "host": None if v is None else f"10.0.0.{i}",
            "loss_pct": 100.0 if v is None else v,
        }
        for i, v in enumerate(losses)
    ]


def test_parse_mtr_json():
    h = parse_mtr_json(json.dumps(MTR))
    assert [x["hop"] for x in h] == [1, 2, 3, 4]
    assert h[1]["host"] is None and h[2]["avg"] == 6.0 and h[0]["sent"] == 10


@pytest.mark.parametrize(
    ("losses", "expected"),
    [
        ((0, 0, 0), None),
        ((0, 100, 0), None),
        ((0, 50, 20, 10), 2),
        ((0, 0, 40), 3),
        ((0, 30, 0, 20), 4),
        ((0, None, 30, 20), 3),  # silent hop ignored
        ((0, 10, None), 2),
        ((None, None), None),
    ],
)
def test_first_persistent_loss(losses, expected):
    assert first_persistent_loss(hops(*losses)) == expected


def fake_runner(rc=0, out=None, err=""):
    calls = []

    async def run(cmd, limit_s):
        calls.append(cmd)
        return rc, json.dumps(MTR) if out is None else out, err

    run.calls = calls
    return run


async def test_run_stores_result_and_detects_path_change(store):
    ps = PathStore(store.engine)
    runner = fake_runner()
    pt = PathTester(ps, cycles=5, runner=runner)
    r = await pt.run("1.1.1.1", trigger="manual")
    assert runner.calls[0][:2] == ["mtr", "--json"] and runner.calls[0][-1] == "1.1.1.1"
    assert "5" in runner.calls[0]
    assert r["status"] == "ok" and r["hop_count"] == 4 and r["reached"] is True
    assert r["first_loss_hop"] == 3 and r["path_changed"] is None
    r2 = await pt.run("1.1.1.1", trigger="scheduled")
    assert r2["path_changed"] is False
    other = json.loads(json.dumps(MTR))
    other["report"]["hubs"][2]["host"] = "10.99.99.99"
    pt.runner = fake_runner(out=json.dumps(other))
    assert (await pt.run("1.1.1.1", trigger="scheduled"))["path_changed"] is True


async def test_run_errors(store):
    ps = PathStore(store.engine)
    r = await PathTester(ps, runner=fake_runner(rc=1, out="", err="mtr: unknown host")).run(
        "x.invalid", trigger="manual"
    )
    assert r["status"] == "error" and "unknown host" in r["error"]
    r = await PathTester(ps, runner=fake_runner(out="not json")).run("1.1.1.1", trigger="manual")
    assert r["status"] == "error" and "unparseable" in r["error"]

    async def missing(cmd, limit_s):
        raise FileNotFoundError

    r = await PathTester(ps, runner=missing).run("1.1.1.1", trigger="manual")
    assert r["error"] == "mtr is not installed"


async def test_outage_trigger_cooldown_and_kinds(store):
    import asyncio

    ps = PathStore(store.engine)
    runner = fake_runner()
    pt = PathTester(ps, cooldown_s=300, runner=runner)
    t = make_target(store, host="1.1.1.1")
    s = TargetSpec(id=t["id"], name="cf", kind="icmp", host="1.1.1.1", interval_s=2, timeout_s=2)
    pt.trigger_for_outage(s, event_id=None)
    pt.trigger_for_outage(s, event_id=None)  # within cooldown
    dns = TargetSpec(id=t["id"], name="dns", kind="dns", host="8.8.8.8", interval_s=10, timeout_s=3)
    pt.trigger_for_outage(dns, None)
    await asyncio.sleep(0.05)
    assert len(runner.calls) == 1
    assert ps.list()[0]["trigger"] == "outage"
    await pt.stop()
