"""Frame parsing (ARP, DHCP, mDNS, SSDP), the kernel packet filter, and frames NetWatch sends.

Everything here is pure: bytes in, observations out, so it is tested with captured frames
without needing a raw socket.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
import struct
from dataclasses import dataclass, field

import dns.exception
import dns.message
import dns.rdatatype

ETH_P_ARP, ETH_P_IP, ETH_P_8021Q = 0x0806, 0x0800, 0x8100
DHCP_PORTS = (67, 68)
MDNS_PORT, SSDP_PORT = 5353, 1900
DHCP_MAGIC = b"\x63\x82\x53\x63"
BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"

DHCP_TYPES = {
    1: "discover",
    2: "offer",
    3: "request",
    4: "decline",
    5: "ack",
    6: "nak",
    7: "release",
    8: "inform",
}


@dataclass
class Observation:
    """One thing learnt about one device (identified by MAC) from one packet."""

    ts_ms: int
    proto: str  # arp | dhcp | mdns | ssdp | neigh
    mac: str
    ip: str | None = None  # an address the device is confirmed to be using
    outgoing: bool = False  # sent by this host
    info: dict = field(default_factory=dict)


def mac_str(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)


def mac_bytes(mac: str) -> bytes:
    return bytes(int(p, 16) for p in mac.split(":"))


def _ip(b: bytes) -> str:
    return socket.inet_ntoa(b)


def usable_ip(ip: str | None) -> bool:
    """A unicast IPv4 address a device can actually hold (not 0.0.0.0, broadcast, multicast)."""
    if not ip:
        return False
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    return not (a.is_unspecified or a.is_multicast or a.is_loopback or ip == "255.255.255.255")


def usable_mac(mac: str) -> bool:
    b = mac_bytes(mac)
    return b != b"\x00" * 6 and not b[0] & 1  # not zero, not multicast/broadcast


# ── frame parsing ─────────────────────────────────────────────────────────────


def parse_frame(frame: bytes, ts_ms: int, *, outgoing: bool = False) -> list[Observation]:
    """Parse one Ethernet frame. Returns [] for anything not understood (never raises)."""
    try:
        return _parse_frame(frame, ts_ms, outgoing)
    except (struct.error, IndexError, ValueError, UnicodeError):
        return []


def _parse_frame(frame: bytes, ts_ms: int, outgoing: bool) -> list[Observation]:
    if len(frame) < 14:
        return []
    eth_src = mac_str(frame[6:12])
    (etype,) = struct.unpack_from("!H", frame, 12)
    off = 14
    if etype == ETH_P_8021Q:
        (etype,) = struct.unpack_from("!H", frame, 16)
        off = 18
    if etype == ETH_P_ARP:
        obs = parse_arp(frame[off:], ts_ms)
        if obs:
            obs.outgoing = outgoing
            obs.info["eth_src"] = eth_src
        return [obs] if obs else []
    if etype != ETH_P_IP:
        return []
    ip = frame[off:]
    if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 17:
        return []
    ihl = (ip[0] & 0x0F) * 4
    (frag,) = struct.unpack_from("!H", ip, 6)
    if frag & 0x1FFF or frag & 0x2000:  # fragments carry no complete UDP payload
        return []
    src_ip, dst_ip = _ip(ip[12:16]), _ip(ip[16:20])
    udp = ip[ihl:]
    if len(udp) < 8:
        return []
    sport, dport, ulen = struct.unpack_from("!HHH", udp, 0)
    payload = udp[8:ulen] if 8 <= ulen <= len(udp) else udp[8:]
    ports = (sport, dport)
    out: list[Observation] = []
    if sport in DHCP_PORTS and dport in DHCP_PORTS:
        out = parse_dhcp(payload, ts_ms, eth_src=eth_src, src_ip=src_ip)
    elif MDNS_PORT in ports:
        out = parse_mdns(payload, ts_ms, eth_src=eth_src, src_ip=src_ip)
    elif SSDP_PORT in ports:
        out = parse_ssdp(payload, ts_ms, eth_src=eth_src, src_ip=src_ip, dst_ip=dst_ip)
    for o in out:
        o.outgoing = outgoing
    return out


def parse_arp(arp: bytes, ts_ms: int) -> Observation | None:
    if len(arp) < 28:
        return None
    htype, ptype, hlen, plen, op = struct.unpack_from("!HHBBH", arp, 0)
    if htype != 1 or ptype != ETH_P_IP or hlen != 6 or plen != 4:
        return None
    sha, spa, tha, tpa = arp[8:14], _ip(arp[14:18]), arp[18:24], _ip(arp[24:28])
    mac = mac_str(sha)
    if not usable_mac(mac):
        return None
    return Observation(
        ts_ms,
        "arp",
        mac,
        ip=spa if usable_ip(spa) else None,  # 0.0.0.0 = ARP probe (address conflict check)
        info={"op": op, "sender_ip": spa, "target_ip": tpa, "target_mac": mac_str(tha)},
    )


def _dhcp_options(data: bytes) -> dict[int, bytes]:
    opts: dict[int, bytes] = {}
    i = 0
    while i < len(data):
        code = data[i]
        if code == 0:
            i += 1
            continue
        if code == 255 or i + 1 >= len(data):
            break
        n = data[i + 1]
        opts.setdefault(code, data[i + 2 : i + 2 + n])
        i += 2 + n
    return opts


def clean_name(raw: bytes | str | None, limit: int = 63) -> str | None:
    """A printable, trimmed device/host name, or None."""
    if raw is None:
        return None
    s = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    s = "".join(ch for ch in s if ch.isprintable()).strip().strip("\x00").strip(".")
    return s[:limit] or None


def parse_dhcp(p: bytes, ts_ms: int, *, eth_src: str, src_ip: str) -> list[Observation]:
    if len(p) < 240 or p[236:240] != DHCP_MAGIC or p[1] != 1 or p[2] != 6:
        return []
    op = p[0]
    (xid,) = struct.unpack_from("!I", p, 4)
    ciaddr, yiaddr = _ip(p[12:16]), _ip(p[16:20])
    chaddr = mac_str(p[28:34])
    opts = _dhcp_options(p[240:])
    mtype = DHCP_TYPES.get(opts.get(53, b"\x00")[0] if opts.get(53) else 0, "unknown")
    info: dict = {"op": op, "type": mtype, "xid": xid, "client_mac": chaddr}
    if 54 in opts and len(opts[54]) == 4:
        info["server_id"] = _ip(opts[54])
    if op == 1:  # client → server
        if not usable_mac(chaddr):
            return []
        if 12 in opts:
            info["hostname"] = clean_name(opts[12])
        if 60 in opts:
            info["vendor_class"] = clean_name(opts[60], 100)
        if 55 in opts:
            info["prl"] = ",".join(str(b) for b in opts[55])
        if 50 in opts and len(opts[50]) == 4:
            info["requested_ip"] = _ip(opts[50])
        return [
            Observation(ts_ms, "dhcp", chaddr, ip=ciaddr if usable_ip(ciaddr) else None, info=info)
        ]
    # server → client: the server itself, plus (on an ACK) the address the client now holds
    info["server_ip"] = info.get("server_id") or src_ip
    if 51 in opts and len(opts[51]) == 4:
        info["lease_s"] = struct.unpack("!I", opts[51])[0]
    out = [Observation(ts_ms, "dhcp", eth_src, ip=src_ip if usable_ip(src_ip) else None, info=info)]
    if mtype == "ack" and usable_ip(yiaddr) and usable_mac(chaddr):
        out.append(
            Observation(ts_ms, "dhcp", chaddr, ip=yiaddr, info={"type": "assigned", "xid": xid})
        )
    return out


_MODEL_KEYS = ("model", "md", "am", "ty", "usb_mdl", "mdl", "product")
_FRIENDLY_KEYS = ("fn", "n", "name")


def _txt(rdata) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in rdata.strings:
        k, _, v = s.decode("utf-8", "replace").partition("=")
        if k:
            out[k.lower()] = v
    return out


def _strip_local(name: str) -> str:
    name = name.rstrip(".")
    return name[: -len(".local")] if name.lower().endswith(".local") else name


def parse_mdns(p: bytes, ts_ms: int, *, eth_src: str, src_ip: str) -> list[Observation]:
    """mDNS responses: the responder's names, advertised services and model.

    Only records that belong to the responder are kept: a Bonjour sleep proxy answers for
    other (sleeping) machines, so names must resolve to the packet's source address and
    service instances must point at one of those names.
    """
    try:
        msg = dns.message.from_wire(p, ignore_trailing=True)
    except (dns.exception.DNSException, ValueError):
        return []
    if not msg.flags & 0x8000 or not usable_mac(eth_src):  # responses only
        return []
    rrsets = list(msg.answer) + list(msg.additional)
    own_names: set[str] = set()
    display: dict[str, str] = {}
    srv_target: dict[str, str] = {}
    for rr in rrsets:
        name = rr.name.to_text().lower()
        if rr.rdtype == dns.rdatatype.A and any(r.address == src_ip for r in rr):
            own_names.add(name)
            display[name] = rr.name.to_text()
        elif rr.rdtype == dns.rdatatype.SRV:
            for r in rr:
                srv_target[name] = r.target.to_text().lower()
    sleeping_proxy = bool(srv_target and own_names and not set(srv_target.values()) & own_names)

    services: set[str] = set()
    instances: dict[str, str] = {}
    model = friendly = None
    for rr in rrsets:
        name = rr.name.to_text()
        lname = name.lower()
        if rr.rdtype == dns.rdatatype.PTR:
            for r in rr:
                target = r.target.to_text()
                if lname.startswith("_services._dns-sd._udp"):
                    services.add(_service_type(target))
                elif lname.startswith("_") and ("._tcp." in lname or "._udp." in lname):
                    inst = target.lower()
                    tgt = srv_target.get(inst)
                    if (tgt is None and not sleeping_proxy) or tgt in own_names:
                        services.add(_service_type(lname))
                        instances[_service_type(lname)] = _instance_label(target)
        elif rr.rdtype == dns.rdatatype.TXT:
            tgt = srv_target.get(lname)
            if (tgt is not None and tgt not in own_names) or (tgt is None and sleeping_proxy):
                continue
            for r in rr:
                kv = _txt(r)
                for k in _MODEL_KEYS:
                    if kv.get(k) and not model:
                        model = clean_name(kv[k])
                for k in _FRIENDLY_KEYS:
                    if kv.get(k) and not friendly and "._googlecast." in lname:
                        friendly = clean_name(kv[k])
    names = sorted(_strip_local(display[n]) for n in own_names)
    if not names and not services and not model:
        return []
    info: dict = {"names": names, "services": sorted(services)}
    if model:
        info["model"] = model
    if friendly or instances:
        info["friendly"] = friendly or next(iter(instances.values()))
    return [
        Observation(ts_ms, "mdns", eth_src, ip=src_ip if usable_ip(src_ip) else None, info=info)
    ]


def _service_type(name: str) -> str:
    """'_airplay._tcp.local.' or 'Living Room._airplay._tcp.local.' → '_airplay._tcp'."""
    parts = [p for p in name.rstrip(".").split(".") if p]
    for i, part in enumerate(parts):
        if part.startswith("_") and i + 1 < len(parts) and parts[i + 1] in ("_tcp", "_udp"):
            return f"{part}.{parts[i + 1]}".lower()
    return name.rstrip(".").lower()


def _instance_label(name: str) -> str:
    label = name.rstrip(".").split("._", 1)[0]
    return clean_name(label.replace("\\032", " ")) or label


def parse_ssdp(
    p: bytes, ts_ms: int, *, eth_src: str, src_ip: str, dst_ip: str
) -> list[Observation]:
    if not usable_mac(eth_src):
        return []
    text = p[:2048].decode("utf-8", "replace")
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    start = lines[0].strip().upper() if lines else ""
    if start.startswith("M-SEARCH"):
        kind = "search"
    elif start.startswith("NOTIFY"):
        kind = "notify"
    elif start.startswith("HTTP/1.1 200"):
        kind = "response"
    else:
        return []
    headers: dict[str, str] = {}
    for line in lines[1:]:
        k, sep, v = line.partition(":")
        if sep:
            headers[k.strip().lower()] = v.strip()
    info: dict = {"kind": kind}
    if headers.get("nts", "").lower() == "ssdp:byebye":
        info["byebye"] = True
    for key, label in (("server", "server"), ("nt", "type"), ("st", "type"), ("usn", "usn")):
        if headers.get(key):
            info[label] = clean_name(headers[key], 200)
    if headers.get("user-agent"):
        info["user_agent"] = clean_name(headers["user-agent"], 200)
    return [
        Observation(ts_ms, "ssdp", eth_src, ip=src_ip if usable_ip(src_ip) else None, info=info)
    ]


def parse_proc_arp(text: str, iface: str | None = None) -> list[tuple[str, str, str]]:
    """Complete entries of /proc/net/arp as (ip, mac, device)."""
    out = []
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) < 6:
            continue
        ip, flags, mac, dev = f[0], f[2], f[3].lower(), f[5]
        try:
            complete = int(flags, 16) & 0x2
        except ValueError:
            continue
        if complete and usable_mac(mac) and usable_ip(ip) and (iface is None or dev == iface):
            out.append((ip, mac, dev))
    return out


# ── kernel packet filter (classic BPF) ────────────────────────────────────────

# Opcodes used below (linux/filter.h).
_LDH_ABS, _LDB_ABS, _LDXB_MSH, _LDH_IND = 0x28, 0x30, 0xB1, 0x48
_JEQ_K, _JSET_K, _RET_K = 0x15, 0x45, 0x06
SNAPLEN = 0x40000


def _assemble(prog: list[tuple]) -> list[tuple[int, int, int, int]]:
    """Resolve label jumps: entries are (code, k, jt_label, jf_label) or ("label", name)."""
    labels: dict[str, int] = {}
    pc = 0
    for ins in prog:
        if ins[0] == "label":
            labels[ins[1]] = pc
        else:
            pc += 1
    out: list[tuple[int, int, int, int]] = []
    for ins in prog:
        if ins[0] == "label":
            continue
        code, k, jt, jf = ins
        here = len(out) + 1
        jt_off = labels[jt] - here if isinstance(jt, str) else jt
        jf_off = labels[jf] - here if isinstance(jf, str) else jf
        out.append((code, jt_off, jf_off, k))
    return out


def capture_filter() -> list[tuple[int, int, int, int]]:
    """ARP, or unfragmented IPv4 UDP to/from ports 67, 68, 5353 or 1900. Everything else
    (including all bulk NAS traffic) is dropped in the kernel before reaching NetWatch."""
    ports = (67, 68, MDNS_PORT, SSDP_PORT)
    prog: list[tuple] = [
        (_LDH_ABS, 12, 0, 0),
        (_JEQ_K, ETH_P_ARP, "accept", 0),
        (_JEQ_K, ETH_P_IP, 0, "reject"),
        (_LDB_ABS, 23, 0, 0),
        (_JEQ_K, 17, 0, "reject"),
        (_LDH_ABS, 20, 0, 0),
        (_JSET_K, 0x1FFF, "reject", 0),
        (_LDXB_MSH, 14, 0, 0),
        (_LDH_IND, 14, 0, 0),
        *[(_JEQ_K, p, "accept", 0) for p in ports],
        (_LDH_IND, 16, 0, 0),
        *[(_JEQ_K, p, "accept", 0) for p in ports[:-1]],
        (_JEQ_K, ports[-1], "accept", "reject"),
        ("label", "reject"),
        (_RET_K, 0, 0, 0),
        ("label", "accept"),
        (_RET_K, SNAPLEN, 0, 0),
    ]
    return _assemble(prog)


def pack_filter(prog: list[tuple[int, int, int, int]]) -> bytes:
    return b"".join(struct.pack("HBBI", code, jt, jf, k) for code, jt, jf, k in prog)


# ── frames NetWatch sends (active scanning) ───────────────────────────────────


def arp_request(src_mac: str, src_ip: str, target_ip: str) -> bytes:
    eth = mac_bytes(BROADCAST_MAC) + mac_bytes(src_mac) + struct.pack("!H", ETH_P_ARP)
    arp = struct.pack("!HHBBH", 1, ETH_P_IP, 6, 4, 1)
    arp += mac_bytes(src_mac) + socket.inet_aton(src_ip) + b"\x00" * 6 + socket.inet_aton(target_ip)
    return (eth + arp).ljust(60, b"\x00")


def random_private_mac() -> str:
    b = bytearray(secrets.token_bytes(6))
    b[0] = (b[0] | 0x02) & 0xFE  # locally administered, unicast
    return mac_str(bytes(b))


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def dhcp_discover(src_mac: str, xid: int, client_mac: str) -> bytes:
    """A broadcast DHCPDISCOVER with the broadcast flag set, so every DHCP server on the LAN
    answers with a broadcast OFFER that the capture sees. NetWatch never sends a REQUEST, so no
    lease is taken; the client MAC inside is a random private one (as nmap does), so the
    host's own lease is never involved."""
    bootp = struct.pack("!BBBBIHH", 1, 1, 6, 0, xid, 0, 0x8000)
    bootp += b"\x00" * 16 + mac_bytes(client_mac) + b"\x00" * 10 + b"\x00" * 192 + DHCP_MAGIC
    bootp += bytes([53, 1, 1, 55, 4, 1, 3, 6, 54, 255])
    bootp = bootp.ljust(300, b"\x00")
    udp = struct.pack("!HHHH", 68, 67, 8 + len(bootp), 0) + bootp
    ip = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
        socket.inet_aton("0.0.0.0"), socket.inet_aton("255.255.255.255"),
    )  # fmt: skip
    ip = ip[:10] + struct.pack("!H", _checksum(ip)) + ip[12:]
    eth = mac_bytes(BROADCAST_MAC) + mac_bytes(src_mac) + struct.pack("!H", ETH_P_IP)
    return eth + ip + udp


SSDP_SEARCH = (
    b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
    b"MX: 2\r\nST: ssdp:all\r\nUSER-AGENT: NetWatch\r\n\r\n"
)


def mdns_browse_query() -> bytes:
    """Ask every mDNS responder to list its service types (answers come back unicast)."""
    q = dns.message.make_query("_services._dns-sd._udp.local.", dns.rdatatype.PTR)
    q.id = 0
    q.flags = 0
    return q.to_wire()
