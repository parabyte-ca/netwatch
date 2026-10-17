"""Raw-socket capture and sending on the host interface (needs CAP_NET_RAW, host networking)."""

from __future__ import annotations

import contextlib
import ctypes
import fcntl
import ipaddress
import logging
import socket
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .packets import capture_filter, pack_filter

log = logging.getLogger(__name__)

ETH_P_ALL = 0x0003
SO_ATTACH_FILTER = 26
PACKET_OUTGOING = 4
SIOCGIFADDR, SIOCGIFNETMASK = 0x8915, 0x891B
MAX_SCAN_HOSTS = 1024  # /22


def iface_mac(sys_root: Path, iface: str) -> str | None:
    try:
        mac = (sys_root / "class" / "net" / iface / "address").read_text().strip().lower()
    except OSError:
        return None
    return mac if len(mac) == 17 else None


def iface_ipv4(iface: str) -> ipaddress.IPv4Interface | None:
    """The interface's primary IPv4 address and prefix (via ioctl; no iproute2 needed)."""
    req = struct.pack("256s", iface[:15].encode())
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            addr = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR, req)[20:24])
            mask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFNETMASK, req)[20:24])
    except OSError:
        return None
    return ipaddress.IPv4Interface(f"{addr}/{mask}")


def scan_networks(
    configured: list[str], local: ipaddress.IPv4Interface | None
) -> tuple[list[ipaddress.IPv4Network], list[str]]:
    """Networks NetWatch may sweep, plus notes about anything refused or narrowed.

    Only private ranges are ever scanned, and at most a /22 (1024 addresses) each.
    """
    notes: list[str] = []
    nets: list[ipaddress.IPv4Network] = []
    candidates = configured or ([str(local.network)] if local else [])
    for c in candidates:
        try:
            net = ipaddress.IPv4Network(c, strict=False)
        except ValueError:
            notes.append(f"{c}: not an IPv4 network")
            continue
        if not net.is_private:
            notes.append(f"{net}: not a private range, skipped")
            continue
        if net.num_addresses > MAX_SCAN_HOSTS:
            if local and local.ip in net:
                narrowed = ipaddress.IPv4Network(f"{local.ip}/24", strict=False)
                notes.append(f"{net}: larger than /22, scanning {narrowed} only")
                net = narrowed
            else:
                notes.append(f"{net}: larger than /22, skipped")
                continue
        if net not in nets:
            nets.append(net)
    return nets, notes


class Sniffer:
    """Receives filtered frames on one interface in a background thread.

    The socket is created with protocol 0 (receives nothing), the BPF filter attached, and only
    then bound to the interface, so no unfiltered traffic is ever queued.
    """

    def __init__(self, iface: str, sink: Callable[[int, bytes, bool], None]) -> None:
        self.iface = iface
        self.sink = sink
        self.error: str | None = None
        self.frames = 0
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def open(self) -> None:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, 0)
        try:
            prog = capture_filter()
            buf = ctypes.create_string_buffer(pack_filter(prog))
            fprog = struct.pack("HL", len(prog), ctypes.addressof(buf))
            sock.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, fprog)
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            sock.bind((self.iface, ETH_P_ALL))
            sock.settimeout(1.0)
        except BaseException:
            sock.close()
            raise
        self._sock = sock

    def start(self) -> None:
        try:
            self.open()
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning(
                "packet capture unavailable", extra={"iface": self.iface, "error": self.error}
            )
            return
        self._thread = threading.Thread(target=self._run, name="netwatch-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                frame, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("packet capture stopped", extra={"error": self.error})
                break
            self.frames += 1
            self.sink(int(time.time() * 1000), frame, addr[2] == PACKET_OUTGOING)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._sock:
            self._sock.close()
        self._thread = self._sock = None


class FrameQueue:
    """Thread-safe hand-off from the capture thread to the asyncio side, bounded."""

    def __init__(self, limit: int = 20_000) -> None:
        self._q: deque[tuple[int, bytes, bool]] = deque()
        self.limit = limit
        self.dropped = 0

    def put(self, ts_ms: int, frame: bytes, outgoing: bool) -> None:
        if len(self._q) >= self.limit:
            self.dropped += 1
            return
        self._q.append((ts_ms, frame, outgoing))

    def drain(self) -> list[tuple[int, bytes, bool]]:
        out = []
        with contextlib.suppress(IndexError):
            while True:
                out.append(self._q.popleft())
        return out


class Sender:
    """Sends raw frames (ARP requests, DHCPDISCOVER) out of one interface."""

    def __init__(self, iface: str) -> None:
        self.iface = iface
        self._sock: socket.socket | None = None

    def __enter__(self) -> Sender:
        self._sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, 0)
        self._sock.bind((self.iface, 0))
        return self

    def send(self, frame: bytes) -> None:
        if self._sock is None:
            raise OSError("sender is not open")
        self._sock.send(frame)

    def __exit__(self, *exc) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None


def send_udp(payload: bytes, group: str, port: int, src_ip: str | None, wait_s: float) -> None:
    """Send one multicast datagram and keep the socket open while unicast replies arrive
    (the capture sees them; the socket only stops the kernel answering 'port unreachable')."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        if src_ip:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(src_ip))
            s.bind((src_ip, 0))
        s.sendto(payload, (group, port))
        s.settimeout(0.2)
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            with contextlib.suppress(TimeoutError, OSError):
                s.recv(4096)


class MulticastMembership:
    """Joins the mDNS and SSDP groups so the NIC delivers their traffic to the capture."""

    GROUPS = ("224.0.0.251", "239.255.255.250")

    def __init__(self) -> None:
        self._sock: socket.socket | None = None

    def join(self, local_ip: str) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for g in self.GROUPS:
            mreq = socket.inet_aton(g) + socket.inet_aton(local_ip)
            with contextlib.suppress(OSError):
                self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    def leave(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None
