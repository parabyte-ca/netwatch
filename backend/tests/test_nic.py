from pathlib import Path

from netwatch.services.events import NIC_ERRORS, NIC_LINK, NIC_SPEED, EventLog
from netwatch.services.nic import (
    NicMonitor,
    NicStore,
    interfaces_to_watch,
    physical_members,
    read_nic,
)

T0 = 1_760_000_040.0  # minute aligned + 0 s


def make_iface(root: Path, name: str, *, physical=True, **vals) -> Path:
    d = root / "class" / "net" / name
    (d / "statistics").mkdir(parents=True, exist_ok=True)
    if physical:
        (d / "device").mkdir(exist_ok=True)
    defaults = {
        "operstate": "up",
        "carrier": "1",
        "speed": "2500",
        "duplex": "full",
        "carrier_changes": "2",
    }
    for k, v in {**defaults, **vals}.items():
        (d / k).write_text(f"{v}\n")
    for c in (
        "rx_bytes",
        "tx_bytes",
        "rx_packets",
        "tx_packets",
        "rx_errors",
        "tx_errors",
        "rx_dropped",
        "tx_dropped",
        "rx_crc_errors",
    ):
        (d / "statistics" / c).write_text("0\n")
    return d


def setv(d: Path, **vals):
    for k, v in vals.items():
        target = d / "statistics" / k if (d / "statistics" / k).exists() else d / k
        target.write_text(f"{v}\n")


def test_read_nic(tmp_path):
    d = make_iface(tmp_path, "enp3s0")
    r = read_nic(tmp_path, "enp3s0")
    assert r.operstate == "up" and r.speed == 2500 and r.carrier_changes == 2
    (d / "speed").write_text("-1\n")
    assert read_nic(tmp_path, "enp3s0").speed is None
    assert read_nic(tmp_path, "nope") is None


def test_physical_members_bridge_bond_vlan(tmp_path):
    make_iface(tmp_path, "enp1s0")
    make_iface(tmp_path, "enp2s0")
    bond = make_iface(tmp_path, "bond0", physical=False)
    (bond / "bonding").mkdir()
    (bond / "bonding" / "slaves").write_text("enp1s0 enp2s0\n")
    br = make_iface(tmp_path, "br0", physical=False)
    (br / "brif").mkdir()
    (br / "brif" / "bond0").mkdir()
    (br / "brif" / "veth123").mkdir()
    vlan = make_iface(tmp_path, "vlan10", physical=False)
    (vlan / "lower_br0").mkdir()
    assert physical_members(tmp_path, "br0") == ["enp1s0", "enp2s0"]
    assert physical_members(tmp_path, "vlan10") == ["enp1s0", "enp2s0"]
    assert interfaces_to_watch(tmp_path, [], "br0") == ["br0", "enp1s0", "enp2s0"]
    assert interfaces_to_watch(tmp_path, ["eth9"], "br0") == ["eth9"]
    assert interfaces_to_watch(tmp_path, [], None) == []


def monitor(tmp_path, store, ifaces=("enp3s0",)):
    log = EventLog(store.engine)
    nic = NicMonitor(
        log, NicStore(store.engine), sys_root=tmp_path, interfaces=lambda: list(ifaces)
    )
    return nic, log


def kinds(log):
    return [(e["kind"], e["title"], e["end_ms"] is None) for e in reversed(log.list(0, 2**62))]


def test_link_down_up_and_flap(tmp_path, store):
    d = make_iface(tmp_path, "enp3s0")
    nic, log = monitor(tmp_path, store)
    nic.poll(T0)
    setv(d, operstate="down", carrier="0", carrier_changes=3)
    nic.poll(T0 + 2)
    assert kinds(log) == [(NIC_LINK, "Link down on enp3s0", True)]
    setv(d, operstate="up", carrier="1", carrier_changes=4)
    nic.poll(T0 + 10)
    ev = log.list(0, 2**62)[0]
    assert ev["end_ms"] - ev["start_ms"] == 8000
    # Down and up again between two polls only shows in carrier_changes.
    setv(d, carrier_changes=6)
    nic.poll(T0 + 12)
    flap = log.list(0, 2**62)[0]
    assert flap["title"] == "Link flapped on enp3s0" and flap["details"]["carrier_transitions"] == 2


def test_speed_renegotiation_and_errors(tmp_path, store):
    d = make_iface(tmp_path, "enp3s0")
    nic, log = monitor(tmp_path, store)
    nic.poll(T0)
    setv(d, speed=1000)
    nic.poll(T0 + 2)
    sp = log.list(0, 2**62, kinds=[NIC_SPEED])[0]
    assert sp["severity"] == "warning" and sp["details"]["to_speed"] == 1000
    setv(d, rx_crc_errors=5, rx_errors=5)
    nic.poll(T0 + 4)
    setv(d, rx_crc_errors=7, rx_errors=7)
    nic.poll(T0 + 6)  # same minute: accumulated, not reported again
    errs = log.list(0, 2**62, kinds=[NIC_ERRORS])
    assert len(errs) == 1 and errs[0]["details"] == {"rx_errors": 5, "rx_crc_errors": 5}
    setv(d, rx_dropped=50)  # drops alone are not an event
    nic.poll(T0 + 62)
    errs = log.list(0, 2**62, kinds=[NIC_ERRORS])
    assert len(errs) == 2 and errs[0]["details"] == {"rx_errors": 2, "rx_crc_errors": 2}


def test_minute_rows_and_counter_reset(tmp_path, store):
    d = make_iface(tmp_path, "enp3s0")
    nic, _ = monitor(tmp_path, store)
    nic.poll(T0)
    setv(d, rx_bytes=1000, tx_bytes=500)
    nic.poll(T0 + 30)
    setv(d, rx_bytes=1500)
    nic.poll(T0 + 61)  # next minute: previous minute flushed
    setv(d, rx_bytes=100)  # counter reset
    nic.poll(T0 + 125)
    rows = NicStore(store.engine).history("enp3s0", 0, 2**31)
    assert [r["rx_bytes"] for r in rows] == [1500, 100]
    assert rows[0]["tx_bytes"] == 500 and rows[0]["speed"] == 2500
    assert nic.status()[0]["operstate"] == "up"


def test_starts_with_link_down(tmp_path, store):
    make_iface(tmp_path, "enp3s0", operstate="down", carrier="0")
    nic, log = monitor(tmp_path, store)
    nic.poll(T0)
    assert kinds(log) == [(NIC_LINK, "Link down on enp3s0", True)]


def test_missing_interface_reported(tmp_path, store):
    nic, _ = monitor(tmp_path, store, ifaces=("ghost0",))
    nic.poll(T0)
    assert nic.status() == [{"iface": "ghost0", "present": False}]
