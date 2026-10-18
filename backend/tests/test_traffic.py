from __future__ import annotations

import time

import pytest

from netwatch.services.traffic import TrafficStore

from .test_deco import MAIN, PASSWORD, PHONE, SAT, TV, FakeDeco, client_for

H = 1_760_000_400  # an hour boundary (seconds)


@pytest.fixture
def ts(store):
    return TrafficStore(store.engine)


def test_record_averages_and_peaks_per_minute(ts):
    ts.record({"aa": (8e6, 1e6)}, H * 1000)
    ts.record({"aa": (16e6, 3e6), "bb": (0, 0)}, H * 1000 + 30_000)
    s = ts.series(["aa"], H, H + 60, 60, minute_floor_s=0)
    assert s == [{"ts": H, "down_bps": 12e6, "up_bps": 2e6, "down_peak": 16e6, "up_peak": 3e6}]
    tot = ts.totals(H, H + 60, minute_floor_s=0)
    assert tot["aa"]["down_bytes"] == pytest.approx(12e6 * 60 / 8) and "bb" not in tot


def test_hourly_rollup_purge_and_totals_across_the_split(ts):
    for m in range(120):  # two hours at 8 Mb/s down
        ts.record({"aa": (8e6, 0)}, (H + m * 60) * 1000)
    assert ts.rollup_hours(H + 2 * 3600 + 5, since_s=H) == 2
    ts.purge(H + 3600, 0)  # minute detail of the first hour is gone
    tot = ts.totals(H, H + 7200, minute_floor_s=H + 3600)
    assert tot["aa"]["down_bytes"] == pytest.approx(2 * 3600 * 8e6 / 8)
    s = ts.series(["aa"], H, H + 7200, 3600, minute_floor_s=H + 3600)
    assert [round(p["down_bps"]) for p in s] == [8_000_000, 8_000_000]


def test_live_top_and_device_traffic_api(client):
    st = client.app.state
    fake = FakeDeco()
    fake.clients[MAIN][0]["down_speed"] = 2500  # KB/s → 20 Mb/s
    fake.clients[MAIN][0]["up_speed"] = 125
    fake.clients[SAT][0]["down_speed"] = 500
    st.deco.client_factory = lambda host, pw: client_for(fake, pw)
    st.deco.save("192.168.2.10", True, PASSWORD)
    client.portal.call(st.deco.poll)
    live = client.get("/api/traffic/live").json()
    assert live["deco_api"] and live["down_bps"] == pytest.approx(24e6)
    top = live["devices"][0]
    assert top["down_bps"] == pytest.approx(20e6) and top["up_bps"] == pytest.approx(1e6)
    assert top["deco_name"] == "Living Room" and top["device_id"]
    assert live["by_deco"]["aa:bb:cc:00:00:01"]["clients"] == 1
    week = client.get("/api/traffic/top?hours=24").json()
    assert week["devices"][0]["label"] == top["label"] and week["down_bytes"] > 0
    d = client.get(f"/api/devices/{top['device_id']}/traffic?hours=1").json()
    assert d["down_bytes"] > 0 and d["series"][-1]["down_bps"] == pytest.approx(20e6)
    assert client.get("/api/devices/99999/traffic").status_code == 404
    # live rates on the map, summed up the tree
    g = client.get("/api/map").json()
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["hub"]["traffic"]["down_bps"] == pytest.approx(24e6, rel=1e-3)
    main = nodes["deco:aa:bb:cc:00:00:01"]["traffic"]
    assert main["down_bps"] == pytest.approx(24e6, rel=1e-3)  # its own + the satellite's
    sat = nodes["deco:aa:bb:cc:00:00:02"]["traffic"]
    assert sat["down_bps"] == pytest.approx(4e6, rel=1e-3)
    edge = next(e for e in g["edges"] if e["target"] == "internet")
    assert edge["traffic"]["down_bps"] == pytest.approx(24e6, rel=1e-3)
    assert PHONE and TV and time.time()


def test_traffic_retention_is_a_setting(client):
    r = client.put("/api/admin/settings", json={"changes": {"traffic_retention_days": 30}})
    assert r.status_code == 200 and client.app.state.settings.traffic_retention_days == 30
