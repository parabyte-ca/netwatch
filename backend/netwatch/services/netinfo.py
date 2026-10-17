"""Host network facts read from /proc (the container shares the host network namespace)."""

from __future__ import annotations

import ipaddress
import socket
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DefaultRoute:
    interface: str
    gateway: str


def parse_default_route(text: str) -> DefaultRoute | None:
    """Parse /proc/net/route content and return the lowest-metric IPv4 default route."""
    best: tuple[int, DefaultRoute] | None = None
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        iface, dest, gw, flags, metric = fields[0], fields[1], fields[2], fields[3], fields[6]
        try:
            if int(dest, 16) != 0 or not int(flags, 16) & 0x2:  # RTF_GATEWAY
                continue
            gateway = socket.inet_ntoa(struct.pack("<L", int(gw, 16)))
            rank = int(metric)
        except ValueError:
            continue
        route = DefaultRoute(iface, gateway)
        if best is None or rank < best[0]:
            best = (rank, route)
    return best[1] if best else None


def default_route(proc_root: Path = Path("/proc")) -> DefaultRoute | None:
    try:
        return parse_default_route((proc_root / "net" / "route").read_text())
    except OSError:
        return None


def is_private(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False
