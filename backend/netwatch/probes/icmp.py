"""ICMP echo probe (needs CAP_NET_RAW when privileged)."""

from __future__ import annotations

from icmplib import async_ping
from icmplib.exceptions import ICMPLibError, NameLookupError, SocketPermissionError

from .base import ProbeResult, TargetSpec, now_ms


async def probe_icmp(t: TargetSpec, *, privileged: bool = True) -> ProbeResult:
    ts = now_ms()
    try:
        host = await async_ping(t.host, count=1, timeout=t.timeout_s, privileged=privileged)
    except NameLookupError:
        return ProbeResult(t.id, ts, False, error="dns_lookup")
    except SocketPermissionError:
        return ProbeResult(t.id, ts, False, error="permission")
    except (ICMPLibError, OSError) as exc:
        return ProbeResult(t.id, ts, False, error=type(exc).__name__[:64])
    if host.is_alive and host.rtts:
        return ProbeResult(t.id, ts, True, rtt_ms=round(host.rtts[0], 3))
    return ProbeResult(t.id, ts, False, error="timeout")
