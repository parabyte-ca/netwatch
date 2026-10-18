"""A LAN host spoofing addresses must not exhaust memory, the database or the timeline."""

from __future__ import annotations

import pytest

from netwatch.db import make_engine, run_migrations
from netwatch.services.discovery import inventory as inv_mod
from netwatch.services.discovery.anomalies import (
    MAX_PER_TYPE_PER_HOUR,
    AnomalyDetector,
)
from netwatch.services.discovery.inventory import DeviceStore, Inventory
from netwatch.services.discovery.packets import Observation
from netwatch.services.events import ANOMALY, EventLog

GW = "192.168.2.1"


@pytest.fixture
def engine(tmp_path):
    e = make_engine(tmp_path / "f.db")
    run_migrations(e)
    yield e
    e.dispose()


def mac(i: int) -> str:
    return "02:00:" + ":".join(f"{(i >> s) & 0xFF:02x}" for s in (24, 16, 8, 0))


def test_new_devices_are_capped_per_minute(engine):
    inv = Inventory(DeviceStore(engine))
    obs = [Observation(1_000_000 + i, "arp", mac(i), ip="192.168.2.50") for i in range(500)]
    changes = inv.apply(obs)
    assert sum(c.kind == "new" for c in changes) == inv_mod.MAX_NEW_PER_MINUTE
    assert inv.flood_dropped == 500 - inv_mod.MAX_NEW_PER_MINUTE
    # a minute later, new devices are recorded again
    later = inv.apply([Observation(1_070_000, "arp", mac(9999), ip="192.168.2.51")])
    assert [c.kind for c in later][:1] == ["new"]


def test_cache_is_bounded(engine, monkeypatch):
    monkeypatch.setattr(inv_mod, "MAX_CACHE", 50)
    monkeypatch.setattr(inv_mod, "MAX_NEW_PER_MINUTE", 10_000)
    inv = Inventory(DeviceStore(engine))
    inv.apply(Observation(1_000_000 + i, "arp", mac(i)) for i in range(200))
    assert len(inv._cache) == 50


def test_bad_observation_does_not_drop_the_batch(engine):
    inv = Inventory(DeviceStore(engine))
    changes = inv.apply(
        [Observation(1, "arp", "not-a-mac"), Observation(2, "arp", mac(1), ip="192.168.2.9")]
    )
    assert any(c.device["mac"] == mac(1) for c in changes)


def test_anomalies_are_capped_per_type_and_state_pruned(engine):
    ev = EventLog(engine)
    det = AnomalyDetector(ev, gateway_ip=lambda: GW)
    det.observe(Observation(1_000_000, "arp", "aa:aa:aa:aa:aa:01", ip=GW))
    for i in range(300):  # the gateway "changes" address on every packet
        det.observe(Observation(1_000_100 + i, "arp", mac(i), ip=GW))
        det.observe(Observation(1_000_100 + i, "arp", mac(i), ip=f"192.168.3.{i % 250}"))
        det.observe(Observation(1_000_100 + i, "arp", mac(i + 1000), ip=f"192.168.3.{i % 250}"))
    found = ev.list(0, 10**13, kinds=[ANOMALY], limit=5000)
    by_type: dict[str, int] = {}
    for e in found:
        by_type[e["details"]["type"]] = by_type.get(e["details"]["type"], 0) + 1
    assert by_type["gateway_mac"] == 1  # one per gateway, not per spoofed address
    assert by_type["duplicate_ip"] <= MAX_PER_TYPE_PER_HOUR
    det.tick(1_000_000 + 3_600_000)
    assert len(det._claims) == 0


def test_long_mdns_names_do_not_rewrite_the_row(engine):
    store = DeviceStore(engine)
    inv = Inventory(store)
    long = "Living-Room-Sonos-Arc-With-A-Very-Long-Name-That-Goes-On-Forever-123456"
    inv.apply([Observation(1, "mdns", mac(5), info={"names": [long]})])
    before = store.get_by_mac(mac(5))
    changes = inv.apply([Observation(70_000, "mdns", mac(5), info={"names": [long]})])
    assert not [c for c in changes if c.kind == "updated"]
    assert store.get_by_mac(mac(5))["mdns_name"] == before["mdns_name"] == long[:64]


def test_unparsable_frames_are_counted_not_fatal(client):
    svc = client.app.state.discovery
    changes = svc._process([(1, b"\x00" * 3, False), (2, None, False)], 1_000)
    assert changes == [] and svc.counts["unparsable"] >= 1


def test_retarget_stays_on_local_networks(client):
    import asyncio

    st = client.app.state
    t = st.store.create_target(
        {"name": "PC", "kind": "icmp", "host": "192.168.2.20", "interval_s": 5, "timeout_s": 1,
         "enabled": True, "group": "device", "auto": False}
    )  # fmt: skip
    dev = {"id": 1, "mac": mac(1), "target_id": t["id"], "name": "PC"}
    client.portal.call(lambda: st.discovery.retarget(dev, "8.8.8.8", old_ip="192.168.2.20"))
    assert st.store.get_target(t["id"])["host"] == "192.168.2.20"
    assert asyncio.iscoroutinefunction(st.discovery.retarget)
