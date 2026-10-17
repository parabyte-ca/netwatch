"""Builders for real-format Ethernet frames used by the discovery tests."""

from __future__ import annotations

import socket
import struct

import dns.message
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.rrset

from netwatch.services.discovery.packets import DHCP_MAGIC, mac_bytes


def eth(dst: str, src: str, etype: int, payload: bytes) -> bytes:
    return mac_bytes(dst) + mac_bytes(src) + struct.pack("!H", etype) + payload


def arp(op: int, sha: str, spa: str, tpa: str, tha: str = "00:00:00:00:00:00") -> bytes:
    body = struct.pack("!HHBBH", 1, 0x0800, 6, 4, op)
    body += mac_bytes(sha) + socket.inet_aton(spa) + mac_bytes(tha) + socket.inet_aton(tpa)
    dst = "ff:ff:ff:ff:ff:ff" if op == 1 else tha
    return eth(dst, sha, 0x0806, body)


def udp(
    src_mac: str,
    src_ip: str,
    dst_ip: str,
    sport: int,
    dport: int,
    payload: bytes,
    *,
    dst_mac: str = "ff:ff:ff:ff:ff:ff",
    proto: int = 17,
    frag: int = 0,
) -> bytes:
    u = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(u), 0, frag, 64, proto, 0,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip),
    )  # fmt: skip
    return eth(dst_mac, src_mac, 0x0800, ip + u)


def dhcp(
    op: int,
    mtype: int,
    chaddr: str,
    *,
    xid: int = 0x1234,
    yiaddr: str = "0.0.0.0",
    ciaddr: str = "0.0.0.0",
    options: dict[int, bytes] | None = None,
) -> bytes:
    p = struct.pack("!BBBBIHH", op, 1, 6, 0, xid, 0, 0x8000)
    p += socket.inet_aton(ciaddr) + socket.inet_aton(yiaddr) + b"\x00" * 8
    p += mac_bytes(chaddr) + b"\x00" * 10 + b"\x00" * 192 + DHCP_MAGIC
    opts = {53: bytes([mtype]), **(options or {})}
    for code, val in opts.items():
        p += bytes([code, len(val)]) + val
    return p + b"\xff"


def dhcp_client_frame(mac: str, mtype: int = 1, **kw) -> bytes:
    return udp(mac, "0.0.0.0", "255.255.255.255", 68, 67, dhcp(1, mtype, mac, **kw))


def dhcp_server_frame(
    server_mac: str, server_ip: str, client_mac: str, mtype: int = 2, yiaddr: str = "0.0.0.0"
) -> bytes:
    payload = dhcp(
        2, mtype, client_mac, yiaddr=yiaddr,
        options={54: socket.inet_aton(server_ip), 51: struct.pack("!I", 86400)},
    )  # fmt: skip
    return udp(server_mac, server_ip, "255.255.255.255", 67, 68, payload)


def mdns_response(records: list[tuple[str, str, str]]) -> bytes:
    """records: (name, rdtype, rdata text)."""
    m = dns.message.Message(id=0)
    m.flags = 0x8400
    for name, rdtype, text in records:
        rd = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.from_text(rdtype), text)
        m.answer.append(dns.rrset.from_rdata(name, 120, rd))
    return m.to_wire()


def mdns_frame(src_mac: str, src_ip: str, records: list[tuple[str, str, str]]) -> bytes:
    return udp(
        src_mac, src_ip, "224.0.0.251", 5353, 5353, mdns_response(records),
        dst_mac="01:00:5e:00:00:fb",
    )  # fmt: skip


def ssdp_frame(src_mac: str, src_ip: str, server: str, nt: str) -> bytes:
    body = (
        "NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nNTS: ssdp:alive\r\n"
        f"SERVER: {server}\r\nNT: {nt}\r\nUSN: uuid:1234::{nt}\r\n\r\n"
    ).encode()
    return udp(src_mac, src_ip, "239.255.255.250", 1900, 1900, body, dst_mac="01:00:5e:7f:ff:fa")
