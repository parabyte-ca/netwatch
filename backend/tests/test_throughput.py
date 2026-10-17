import json
import time

import pytest

from netwatch.services.throughput import ThroughputStore, ThroughputTester, parse_iperf_json

OK = {
    "start": {"connecting_to": {"host": "192.168.2.2", "port": 5201}},
    "end": {
        "sum_sent": {"seconds": 10.0, "bits_per_second": 941_000_000.0, "retransmits": 12},
        "sum_received": {"seconds": 10.0, "bits_per_second": 938_000_000.0},
        "cpu_utilization_percent": {"host_total": 5.0},
    },
}


def test_parse_iperf_json():
    p = parse_iperf_json(json.dumps(OK))
    assert (
        p["bits_per_second"] == 938_000_000.0 and p["retransmits"] == 12 and p["duration_s"] == 10.0
    )
    with pytest.raises(ValueError, match="unable to connect"):
        parse_iperf_json(json.dumps({"error": "unable to connect to server: Connection refused"}))
    with pytest.raises(ValueError):
        parse_iperf_json(json.dumps({"end": {}}))


def server(store, **over):
    values = {
        "name": "PC",
        "host": "192.168.2.2",
        "port": 5201,
        "kind": "lan",
        "duration_s": 5,
        "parallel": 1,
        "interval_min": 0,
        "enabled": True,
    }
    values.update(over)
    return ThroughputStore(store.engine).create_server(values)


def runner_with(out):
    calls = []

    async def run(cmd, limit_s):
        calls.append(cmd)
        return 0, out, ""

    run.calls = calls
    return run


async def test_run_server_both_directions(store):
    ts = ThroughputStore(store.engine)
    s = server(store)
    runner = runner_with(json.dumps(OK))
    tester = ThroughputTester(ts, runner=runner)
    down, up = await tester.run_server(s, trigger="manual")
    assert "-R" in runner.calls[0] and "-R" not in runner.calls[1]
    assert runner.calls[0][:3] == ["iperf3", "-c", "192.168.2.2"]
    assert down["direction"] == "download" and up["direction"] == "upload"
    assert down["status"] == "ok" and down["bits_per_second"] == 938_000_000.0
    assert ts.list_tests(server_id=s["id"])[0]["id"] == up["id"]


async def test_run_server_error(store):
    ts = ThroughputStore(store.engine)
    s = server(store)
    tester = ThroughputTester(
        ts, runner=runner_with(json.dumps({"error": "the server is busy running a test"}))
    )
    results = await tester.run_server(s, trigger="manual")
    assert all(r["status"] == "error" and "busy" in r["error"] for r in results)


async def test_scheduled_pass(store):
    ts = ThroughputStore(store.engine)
    server(store, name="manual")
    s = server(store, name="hourly", interval_min=60)
    runner = runner_with(json.dumps(OK))
    tester = ThroughputTester(ts, runner=runner)
    now = time.time()  # results are stamped with the real clock
    assert await tester._scheduled_pass(now=now) == 1
    assert await tester._scheduled_pass(now=now + 1800) == 0
    assert await tester._scheduled_pass(now=now + 3700) == 1
    assert len(ts.list_tests(server_id=s["id"])) == 4
