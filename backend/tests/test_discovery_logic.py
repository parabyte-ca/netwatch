from __future__ import annotations

import pytest

from netwatch.db import make_engine, run_migrations
from netwatch.services.discovery.anomalies import AnomalyDetector
from netwatch.services.discovery.classify import (
    classify,
    is_randomised,
    mergeable_name,
    short_vendor,
    vendor_for,
)
from netwatch.services.discovery.inventory import DeviceStore, Inventory
from netwatch.services.discovery.packets import Observation, parse_frame
from netwatch.services.events import EventLog

from .frames import arp, dhcp_client_frame, dhcp_server_frame, mdns_frame

HUB = "a0:b5:49:00:00:01"
DECO = "3c:84:6a:00:00:02"  # TP-Link OUI
SYNO = "00:11:32:aa:bb:cc"  # Synology OUI
PHONE_A = "02:11:22:33:44:55"
PHONE_B = "06:aa:bb:cc:dd:ee"
GW = "192.168.2.1"


@pytest.fixture
def engine(tmp_path):
    e = make_engine(tmp_path / "d.db")
    run_migrations(e)
    yield e
    e.dispose()


@pytest.fixture
def inv(engine):
    return Inventory(DeviceStore(engine), own_macs=lambda: {SYNO}, gateway_ip=lambda: GW)


@pytest.fixture
def events(engine):
    return EventLog(engine)


def frames(*fs, ts=1_000_000):
    out = []
    for i, f in enumerate(fs):
        out.extend(parse_frame(f, ts + i))
    return out


# ── classification ────────────────────────────────────────────────────────────


def test_vendor_lookup_and_cleanup():
    assert vendor_for(DECO) == "TP-Link"
    assert vendor_for(SYNO) == "Synology"
    assert vendor_for(PHONE_A) is None  # private address: no vendor
    assert is_randomised(PHONE_A) and not is_randomised(DECO)
    assert short_vendor("Apple, Inc.") == "Apple"
    assert short_vendor("Raspberry Pi Trading Ltd") == "Raspberry Pi"


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"names": ["Deco-X55"]}, "access_point"),
        ({"names": ["Janes-iPhone"]}, "phone"),
        ({"services": ["_googlecast._tcp"]}, "media_player"),
        ({"services": ["_ipp._tcp"]}, "printer"),
        ({"dhcp_vendor": "android-dhcp-14"}, "phone"),
        ({"dhcp_vendor": "MSFT 5.0"}, "computer"),
        ({"ssdp": ["urn:schemas-upnp-org:device:InternetGatewayDevice:1"]}, "router"),
        ({"vendor": "Espressif"}, "iot"),
        ({"is_gateway": True}, "router"),
        ({"is_self": True}, "nas"),
        ({}, "unknown"),
    ],
)
def test_classify(kw, expected):
    base = {
        "vendor": None,
        "names": [],
        "services": [],
        "dhcp_vendor": None,
        "ssdp": [],
        "is_gateway": False,
        "is_self": False,
        "is_dhcp_server": False,
    }
    t, reason = classify(**{**base, **kw})
    assert t == expected and reason


def test_mergeable_names():
    assert mergeable_name("Janes-iPhone") == "janes-iphone"
    assert mergeable_name("iPhone") is None
    assert mergeable_name("3f2a9b1c0d4e") is None  # random-looking
    assert mergeable_name("") is None


# ── inventory ─────────────────────────────────────────────────────────────────


def test_inventory_builds_devices_from_traffic(inv):
    changes = inv.apply(
        frames(
            arp(2, HUB, GW, "192.168.2.15", "00:11:32:aa:bb:cc"),
            dhcp_client_frame(DECO, 3, options={12: b"Deco-X55"}),
            arp(1, DECO, "192.168.2.10", GW),
        )
    )
    assert [c.kind for c in changes] == ["new", "new"]
    rows = {r["mac"]: r for r in inv.listing(1_000_010)}
    assert rows[HUB]["type"] == "router" and rows[HUB]["last_ip"] == GW
    deco = rows[DECO]
    assert deco["hostname"] == "Deco-X55" and deco["type"] == "access_point"
    assert deco["last_ip"] == "192.168.2.10" and deco["online"]
    assert deco["label"] == "Deco-X55"


def test_ip_history_and_change(inv):
    inv.apply(frames(arp(1, DECO, "192.168.2.10", GW)))
    changes = inv.apply(frames(arp(1, DECO, "192.168.2.11", GW), ts=2_000_000))
    assert [c.kind for c in changes] == ["ip"] and changes[0].old_ip == "192.168.2.10"
    dev = inv.store.get_by_mac(DECO)
    ips = [r["ip"] for r in inv.store.ips([dev["id"]])]
    assert ips == ["192.168.2.11", "192.168.2.10"]


def test_last_seen_writes_are_throttled(inv, engine):
    inv.apply(frames(arp(1, DECO, "192.168.2.10", GW), ts=1_000_000))
    inv.apply(frames(arp(1, DECO, "192.168.2.10", GW), ts=1_010_000))
    assert inv.store.get_by_mac(DECO)["last_seen_ms"] == 1_000_000  # within a minute: no write
    inv.apply(frames(arp(1, DECO, "192.168.2.10", GW), ts=1_070_000))
    assert inv.store.get_by_mac(DECO)["last_seen_ms"] == 1_070_000


def test_randomised_macs_with_same_hostname_are_merged(inv):
    inv.apply(frames(dhcp_client_frame(PHONE_A, 3, options={12: b"Janes-Pixel"})))
    inv.apply(frames(dhcp_client_frame(PHONE_B, 3, options={12: b"Janes-Pixel"}), ts=5_000_000))
    rows = inv.listing(5_000_100)
    assert len(rows) == 1
    assert rows[0]["macs"] == [PHONE_A, PHONE_B]


def test_generic_hostnames_are_not_merged(inv):
    inv.apply(frames(dhcp_client_frame(PHONE_A, 3, options={12: b"iPhone"})))
    inv.apply(frames(dhcp_client_frame(PHONE_B, 3, options={12: b"iPhone"})))
    assert len(inv.listing(2_000_000)) == 2


def test_mdns_identifies_device(inv):
    inv.apply(
        frames(
            mdns_frame(
                SYNO.replace("00:11", "00:12"),
                "192.168.2.30",
                [
                    ("Living-Room.local.", "A", "192.168.2.30"),
                    ("_googlecast._tcp.local.", "PTR", "Chromecast-1._googlecast._tcp.local."),
                    ("Chromecast-1._googlecast._tcp.local.", "SRV", "0 0 8009 Living-Room.local."),
                    ("Chromecast-1._googlecast._tcp.local.", "TXT", '"md=Chromecast" "fn=Den TV"'),
                ],
            )
        )
    )
    (row,) = inv.listing(1_000_100)
    assert row["label"] == "Den TV" and row["mdns_name"] == "Living-Room"
    assert row["model"] == "Chromecast" and row["type"] == "tv"


def test_purge_keeps_named_devices(inv):
    inv.apply(frames(arp(1, DECO, "192.168.2.10", GW), arp(1, HUB, GW, "192.168.2.10")))
    hub = inv.store.get_by_mac(HUB)
    inv.store.update(hub["id"], {"name": "Home Hub"})
    assert inv.store.purge(2_000_000) == 1
    assert inv.store.get_by_mac(HUB) is not None and inv.store.get_by_mac(DECO) is None


# ── anomalies ─────────────────────────────────────────────────────────────────


def _anomalies(events) -> list[dict]:
    return [e for e in events.list(0, 10**13) if e["kind"] == "anomaly"]


def make_detector(events, kv=None, **kw):
    kv = {} if kv is None else kv
    return AnomalyDetector(
        events,
        gateway_ip=lambda: GW,
        kv_get=lambda k, d=None: kv.get(k, d),
        kv_set=kv.__setitem__,
        **kw,
    )


def test_duplicate_ip(events):
    det = make_detector(events)
    for o in frames(arp(1, DECO, "192.168.2.50", GW), arp(1, SYNO, "192.168.2.50", GW)):
        det.observe(o)
    (ev,) = _anomalies(events)
    assert ev["details"]["type"] == "duplicate_ip" and ev["severity"] == "critical"
    assert ev["details"]["macs"] == sorted([DECO, SYNO])
    # repeated evidence within the cooldown does not spam the timeline
    for o in frames(arp(1, DECO, "192.168.2.50", GW), ts=1_100_000):
        det.observe(o)
    assert len(_anomalies(events)) == 1


def test_same_ip_reused_after_a_while_is_not_duplicate(events):
    det = make_detector(events)
    for o in frames(arp(1, DECO, "192.168.2.50", GW), ts=1_000_000):
        det.observe(o)
    for o in frames(arp(1, SYNO, "192.168.2.50", GW), ts=1_000_000 + 600_000):
        det.observe(o)
    assert _anomalies(events) == []


def test_second_dhcp_server(events):
    det = make_detector(events)
    rogue = "3c:84:6a:00:00:09"
    obs = frames(
        dhcp_server_frame(HUB, GW, PHONE_A, 2),
        dhcp_server_frame(rogue, "192.168.68.1", PHONE_A, 2),
    )
    for o in obs:
        det.observe(o)
    (ev,) = _anomalies(events)
    assert ev["details"]["type"] == "dhcp_server"
    assert {s["ip"] for s in ev["details"]["servers"]} == {GW, "192.168.68.1"}
    assert "Deco" in ev["details"]["hint"]


def test_gateway_mac_change(events):
    kv: dict = {}
    det = make_detector(events, kv)
    for o in frames(arp(2, HUB, GW, "192.168.2.15")):
        det.observe(o)
    assert kv["discovery_gateway"] == {"ip": GW, "mac": HUB} and _anomalies(events) == []
    for o in frames(arp(2, DECO, GW, "192.168.2.15"), ts=9_000_000):
        det.observe(o)
    types = {e["details"]["type"] for e in _anomalies(events)}
    assert "gateway_mac" in types
    assert kv["discovery_gateway"]["mac"] == DECO


def test_new_gateway_address_is_a_new_baseline(events):
    kv: dict = {"discovery_gateway": {"ip": "192.168.1.1", "mac": DECO}}
    det = make_detector(events, kv)
    for o in frames(arp(2, HUB, GW, "192.168.2.15")):
        det.observe(o)
    assert _anomalies(events) == [] and kv["discovery_gateway"] == {"ip": GW, "mac": HUB}


def test_dhcp_churn_counts_exchanges_not_packets(events):
    det = make_detector(events)
    t = 1_000_000
    for i in range(3):  # three rejoin exchanges, each DISCOVER + REQUEST a second apart
        for o in frames(dhcp_client_frame(PHONE_A, 1), dhcp_client_frame(PHONE_A, 3),
                        ts=t + i * 600_000):  # fmt: skip
            det.observe(o)
    assert _anomalies(events) == []
    for o in frames(dhcp_client_frame(PHONE_A, 1), ts=t + 3 * 600_000):
        det.observe(o)
    (ev,) = _anomalies(events)
    assert ev["details"]["type"] == "dhcp_churn" and ev["details"]["joins"] == 4


def test_arp_storm_opens_and_closes(events):
    det = make_detector(events, storm_pps=50)
    base = 1_000_000_000
    for sec in range(12):
        for i in range(80):
            det.observe(Observation(base + sec * 1000 + i, "arp", DECO, ip="192.168.2.10"))
    det.tick(base + 12_000)
    (ev,) = _anomalies(events)
    assert ev["open"] and ev["details"]["type"] == "arp_storm"
    assert ev["details"]["top_senders"][0]["mac"] == DECO
    det.tick(base + 30_000)  # quiet starts
    det.tick(base + 61_000)  # quiet for 30 s
    (ev,) = _anomalies(events)
    assert not ev["open"] and ev["details"]["peak_pps"] >= 50


def test_arp_storm_suppressed_during_own_sweep(events):
    det = make_detector(events, storm_pps=50)
    base = 1_000_000_000
    det.suppress_storm_until_ms = base + 20_000
    for sec in range(12):
        for i in range(80):
            det.observe(Observation(base + sec * 1000 + i, "arp", DECO, ip="192.168.2.10"))
    det.tick(base + 12_000)
    assert _anomalies(events) == []


def test_outgoing_packets_are_ignored(events):
    det = make_detector(events)
    for o in frames(arp(1, DECO, "192.168.2.50", GW), arp(1, SYNO, "192.168.2.50", GW)):
        o.outgoing = True
        det.observe(o)
    assert _anomalies(events) == []


def _path(hops):
    return {"id": 1, "ts_ms": 1, "status": "ok", "host": "1.1.1.1",
            "hops": [{"host": h} for h in hops]}  # fmt: skip


def test_double_nat(events):
    det = make_detector(events)
    det.check_path(_path(["192.168.68.1", "192.168.2.1", "64.230.1.1", "1.1.1.1"]))
    (ev,) = _anomalies(events)
    assert ev["details"]["type"] == "double_nat"


def test_bell_internal_10_hops_are_not_double_nat(events):
    det = make_detector(events)
    det.check_path(_path(["192.168.2.1", "10.11.4.1", "64.230.1.1", "1.1.1.1"]))
    det.check_path(_path(["192.168.2.1", None, "64.230.1.1", "1.1.1.1"]))
    assert _anomalies(events) == []


def test_cgnat(events):
    det = make_detector(events)
    det.check_path(_path(["192.168.2.1", "100.72.0.1", "64.230.1.1"]))
    (ev,) = _anomalies(events)
    assert ev["details"]["type"] == "cgnat" and ev["severity"] == "info"


def test_dns_checks(events):
    det = make_detector(events)
    probe = "netwatch-abc.com"
    results = {
        GW: {"www.google.com": ("ok", ["142.250.1.1"]), "github.com": ("nxdomain", []),
             probe: ("ok", ["67.63.55.3"])},
        "1.1.1.1": {"www.google.com": ("ok", ["142.250.1.2"]), "github.com": ("ok", ["140.82.1.1"]),
                    probe: ("nxdomain", [])},
        "8.8.8.8": {"www.google.com": ("ok", ["10.0.0.5"]), "github.com": ("ok", ["140.82.1.1"]),
                    probe: ("nxdomain", [])},
    }  # fmt: skip
    found = det.check_dns(results, probe, 1)
    types = sorted(f.type for f in found)
    assert types == ["dns_mismatch", "dns_mismatch", "nxdomain_rewrite"]
    # Different CDN addresses for the same name are normal and not reported.
    assert not any("142.250" in f.title for f in found)
