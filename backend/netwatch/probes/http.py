"""HTTP(S) check: total time to response headers, success judged by status code."""

from __future__ import annotations

import httpx

from .base import ProbeResult, Stopwatch, TargetSpec, now_ms


async def probe_http(t: TargetSpec, client: httpx.AsyncClient) -> ProbeResult:
    ts = now_ms()
    sw = Stopwatch()
    try:
        async with client.stream("GET", t.url, timeout=t.timeout_s) as resp:
            rtt = sw.ms()
            status = resp.status_code
    except httpx.TimeoutException:
        return ProbeResult(t.id, ts, False, error="timeout")
    except httpx.ConnectError:
        return ProbeResult(t.id, ts, False, error="connect")
    except httpx.TooManyRedirects:
        return ProbeResult(t.id, ts, False, error="too_many_redirects")
    except httpx.HTTPError as exc:
        return ProbeResult(t.id, ts, False, error=type(exc).__name__[:64])
    ok = status == t.expect_status if t.expect_status else status < 400
    return ProbeResult(t.id, ts, ok, rtt_ms=rtt, error=None if ok else f"http_{status}")
