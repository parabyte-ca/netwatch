from __future__ import annotations

import socket
import struct
import time

import pytest

from netwatch.services.discovery.capture import Sniffer, scan_networks
from netwatch.services.discovery.packets import (
    arp_request,
    capture_filter,
    dhcp_discover,
    mdns_browse_query,
    parse_frame,
    parse_proc_arp,
    random_private_mac,
)

from .frames import (
    arp,
    dhcp_client_frame,
    dhcp_server_frame,
    mdns_frame,
    ssdp_frame,
    udp,
)

PHONE = "02:11:22:33:44:55"  # locally administered (private Wi-Fi address)
PC = "3c:52:82:aa:bb:cc"
HUB = "a0:b5:49:00:00:01"


def run_bpf(prog, pkt: bytes) -> int:
    """Minimal classic-BPF interpreter covering the opcodes capture_filter() uses."""
    a = x = pc = 0
    while True:
        code, jt, jf, k = prog[pc]
        pc += 1
        if code == 0x28:  # ldh [k]
            if k + 2 > len(pkt):
                return 0
            (a,) = struct.unpack_from("!H", pkt, k)
        elif code == 0x30:  # ldb [k]
            if k >= len(pkt):
                return 0
            a = pkt[k]
        elif code == 0xB1:  # ldxb 4*([k]&0xf)
            x = (pkt[k] & 0x0F) * 4
        elif code == 0x48:  # ldh [x+k]
            if x + k + 2 > len(pkt):
                return 0
            (a,) = struct.unpack_from("!H", pkt, x + k)
        elif code == 0x15:  # jeq
            pc += jt if a == k else jf
        elif code == 0x45:  # jset
            pc += jt if a & k else jf
        elif code == 0x06:
            return k
        else:
            raise AssertionError(f"unexpected opcode {code:#x}")


class TestFilter:
    prog = capture_filter()

    @pytest.mark.parametrize(
        "frame",
        [
            arp(1, PC, "192.168.2.20", "192.168.2.1"),
            dhcp_client_frame(PHONE),
            mdns_frame(PC, "192.168.2.20", [("pc.local.", "A", "192.168.2.20")]),
            ssdp_frame(PC, "192.168.2.20", "Linux UPnP/1.0", "upnp:rootdevice"),
            udp(PC, "192.168.2.20", "192.168.2.15", 51515, 1900, b"x", dst_mac=HUB),
        ],
    )
    def test_accepts_discovery_traffic(self, frame):
        assert run_bpf(self.prog, frame) > 0

    @pytest.mark.parametrize(
        "frame",
        [
            udp(PC, "192.168.2.20", "192.168.2.15", 40000, 445, b"x" * 100, proto=6),  # SMB/TCP
            udp(PC, "192.168.2.20", "192.168.2.1", 40000, 53, b"x"),  # DNS
            udp(PC, "192.168.2.20", "192.168.2.1", 1900, 1900, b"x", frag=0x0010),  # fragment
            b"\x00" * 12 + b"\x86\xdd" + b"\x00" * 60,  # IPv6
        ],
    )
    def test_rejects_everything_else(self, frame):
        assert run_bpf(self.prog, frame) == 0


def test_live_capture_on_loopback():
    """The filter and socket setup work against the real kernel (needs root / CAP_NET_RAW)."""
    got: list[bytes] = []
    s = Sniffer("lo", lambda ts, f, out: got.append(f))
    try:
        s.open()
    except PermissionError:
        pytest.skip("no CAP_NET_RAW")
    s._sock.close()
    s.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            u.sendto(b"ignored", ("127.0.0.1", 9))
            u.sendto(b"NOTIFY * HTTP/1.1\r\n\r\n", ("127.0.0.1", 1900))
        deadline = time.time() + 3
        while time.time() < deadline and not any(b"NOTIFY" in f for f in got):
            time.sleep(0.05)
    finally:
        s.stop()
    assert any(b"NOTIFY" in f for f in got)
    assert not any(b"ignored" in f for f in got)


def test_arp_request_and_reply():
    req = parse_frame(arp(1, PC, "192.168.2.20", "192.168.2.1"), 1000)
    assert len(req) == 1 and req[0].mac == PC and req[0].ip == "192.168.2.20"
    assert req[0].info["op"] == 1 and req[0].info["target_ip"] == "192.168.2.1"
    probe = parse_frame(arp(1, PC, "0.0.0.0", "192.168.2.20"), 1000)  # address-conflict probe
    assert probe[0].ip is None


def test_dhcp_client_fields():
    frame = dhcp_client_frame(
        PHONE,
        3,
        options={12: b"Jane-Pixel\x00", 60: b"android-dhcp-14", 55: bytes([1, 3, 6, 15]),
                 50: socket.inet_aton("192.168.2.44")},
    )  # fmt: skip
    (o,) = parse_frame(frame, 5)
    assert o.mac == PHONE and o.ip is None
    assert o.info["type"] == "request"
    assert o.info["hostname"] == "Jane-Pixel"
    assert o.info["vendor_class"] == "android-dhcp-14"
    assert o.info["prl"] == "1,3,6,15"
    assert o.info["requested_ip"] == "192.168.2.44"


def test_dhcp_ack_reports_server_and_assignment():
    obs = parse_frame(dhcp_server_frame(HUB, "192.168.2.1", PHONE, 5, "192.168.2.44"), 5)
    server, client = obs
    assert server.mac == HUB and server.info["server_ip"] == "192.168.2.1"
    assert server.info["lease_s"] == 86400
    assert client.mac == PHONE and client.ip == "192.168.2.44"


def test_mdns_names_services_and_model():
    frame = mdns_frame(
        PC,
        "192.168.2.30",
        [
            ("Living-Room.local.", "A", "192.168.2.30"),
            ("_airplay._tcp.local.", "PTR", "Living\\032Room._airplay._tcp.local."),
            ("Living\\032Room._airplay._tcp.local.", "SRV", "0 0 7000 Living-Room.local."),
            ("Living\\032Room._airplay._tcp.local.", "TXT", '"model=AppleTV11,1"'),
        ],
    )
    (o,) = parse_frame(frame, 5)
    assert o.info["names"] == ["Living-Room"]
    assert o.info["services"] == ["_airplay._tcp"]
    assert o.info["model"] == "AppleTV11,1"
    assert o.info["friendly"] == "Living Room"


def test_mdns_sleep_proxy_records_are_not_attributed():
    """An Apple TV answering for a sleeping Mac must not inherit the Mac's services."""
    frame = mdns_frame(
        PC,
        "192.168.2.30",
        [
            ("Living-Room.local.", "A", "192.168.2.30"),
            ("Office-Mac.local.", "A", "192.168.2.31"),
            ("_smb._tcp.local.", "PTR", "Office._smb._tcp.local."),
            ("Office._smb._tcp.local.", "SRV", "0 0 445 Office-Mac.local."),
        ],
    )
    (o,) = parse_frame(frame, 5)
    assert o.info["names"] == ["Living-Room"]
    assert o.info["services"] == []


def test_ssdp_notify():
    (o,) = parse_frame(ssdp_frame(PC, "192.168.2.40", "Roku/9.4 UPnP/1.0", "roku:ecp"), 5)
    assert o.proto == "ssdp" and o.info["server"] == "Roku/9.4 UPnP/1.0"
    assert o.info["type"] == "roku:ecp" and o.ip == "192.168.2.40"


def test_garbage_never_raises():
    for junk in (b"", b"\x00" * 13, b"\xff" * 64, arp(1, PC, "1.2.3.4", "1.2.3.5")[:20]):
        assert parse_frame(junk, 0) == []


def test_frames_we_send_parse_back():
    (o,) = parse_frame(arp_request(PC, "192.168.2.15", "192.168.2.99"), 0)
    assert o.mac == PC and o.info["target_ip"] == "192.168.2.99"
    client = random_private_mac()
    assert int(client[:2], 16) & 0x03 == 0x02  # locally administered unicast
    frame = dhcp_discover(PC, 0xABCD, client)
    (d,) = parse_frame(frame, 0)
    assert d.info["type"] == "discover" and d.info["xid"] == 0xABCD
    assert d.mac == client and frame[6:12] == bytes.fromhex(PC.replace(":", ""))
    assert run_bpf(capture_filter(), frame) > 0
    assert mdns_browse_query()[:2] == b"\x00\x00"


def test_proc_arp():
    text = (
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.168.2.1      0x1         0x2         a0:b5:49:00:00:01     *        br0\n"
        "192.168.2.50     0x1         0x0         00:00:00:00:00:00     *        br0\n"
        "172.17.0.2       0x1         0x2         02:42:ac:11:00:02     *        docker0\n"
    )
    assert parse_proc_arp(text, "br0") == [("192.168.2.1", HUB, "br0")]


def test_scan_networks_only_private_and_small():
    import ipaddress

    local = ipaddress.IPv4Interface("192.168.2.15/24")
    nets, notes = scan_networks([], local)
    assert [str(n) for n in nets] == ["192.168.2.0/24"]
    nets, notes = scan_networks(["8.8.8.0/24", "10.0.0.0/8", "192.168.0.0/16"], local)
    assert [str(n) for n in nets] == ["192.168.2.0/24"]
    assert any("not a private" in n for n in notes)
    assert any("10.0.0.0/8" in n and "skipped" in n for n in notes)
