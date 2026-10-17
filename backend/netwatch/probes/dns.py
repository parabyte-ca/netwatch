"""DNS query probe: measures resolver latency and failures (one UDP query, no retries)."""

from __future__ import annotations

import dns.asyncquery
import dns.exception
import dns.message
import dns.rcode
import dns.rdatatype

from .base import ProbeResult, Stopwatch, TargetSpec, now_ms

DEFAULT_QUERY = "www.google.com"


async def probe_dns(t: TargetSpec) -> ProbeResult:
    ts = now_ms()
    qname = t.dns_query or DEFAULT_QUERY
    rdtype = dns.rdatatype.from_text(t.dns_rdtype or "A")
    query = dns.message.make_query(qname, rdtype)
    sw = Stopwatch()
    try:
        response = await dns.asyncquery.udp(query, t.host, timeout=t.timeout_s, port=t.port or 53)
    except dns.exception.Timeout:
        return ProbeResult(t.id, ts, False, error="timeout")
    except (dns.exception.DNSException, OSError) as exc:
        return ProbeResult(t.id, ts, False, error=type(exc).__name__[:64])
    rtt = sw.ms()
    rcode = response.rcode()
    if rcode != dns.rcode.NOERROR:
        return ProbeResult(t.id, ts, False, rtt_ms=rtt, error=dns.rcode.to_text(rcode).lower())
    if not response.answer:
        return ProbeResult(t.id, ts, False, rtt_ms=rtt, error="no_answer")
    return ProbeResult(t.id, ts, True, rtt_ms=rtt)
