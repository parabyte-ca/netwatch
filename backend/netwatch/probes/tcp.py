"""TCP connect probe: time to complete the three-way handshake."""

from __future__ import annotations

import asyncio
import contextlib

from .base import ProbeResult, Stopwatch, TargetSpec, now_ms


async def probe_tcp(t: TargetSpec) -> ProbeResult:
    ts = now_ms()
    sw = Stopwatch()
    try:
        async with asyncio.timeout(t.timeout_s):
            _reader, writer = await asyncio.open_connection(t.host, t.port)
    except TimeoutError:
        return ProbeResult(t.id, ts, False, error="timeout")
    except ConnectionRefusedError:
        return ProbeResult(t.id, ts, False, error="refused")
    except OSError as exc:
        return ProbeResult(t.id, ts, False, error=type(exc).__name__[:64])
    rtt = sw.ms()
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return ProbeResult(t.id, ts, True, rtt_ms=rtt)
