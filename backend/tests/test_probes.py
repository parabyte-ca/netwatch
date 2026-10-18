import asyncio
import socket

import dns.message
import dns.rrset
import httpx
import pytest

from netwatch.probes import Prober, TargetSpec
from netwatch.probes import icmp as icmp_mod
from netwatch.probes.dns import probe_dns
from netwatch.probes.http import probe_http
from netwatch.probes.tcp import probe_tcp


def spec(**kw) -> TargetSpec:
    base = {
        "id": 1,
        "name": "t",
        "kind": "icmp",
        "host": "127.0.0.1",
        "interval_s": 5,
        "timeout_s": 1,
    }
    base.update(kw)
    return TargetSpec(**base)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── TCP ──────────────────────────────────────────────────────────────────────


async def test_tcp_connect_ok():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        r = await probe_tcp(spec(kind="tcp", port=port))
    assert r.ok and r.rtt_ms is not None and r.rtt_ms >= 0


async def test_tcp_refused():
    r = await probe_tcp(spec(kind="tcp", port=_free_port()))
    assert not r.ok and r.error == "refused"


# ── HTTP ─────────────────────────────────────────────────────────────────────


def _client(status: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status)))


async def test_http_ok_and_status_rules():
    async with _client(200) as c:
        assert (await probe_http(spec(kind="http", url="http://x.test/"), c)).ok
    async with _client(503) as c:
        r = await probe_http(spec(kind="http", url="http://x.test/"), c)
        assert not r.ok and r.error == "http_503" and r.rtt_ms is not None
    async with _client(204) as c:
        assert (await probe_http(spec(kind="http", url="http://x.test/", expect_status=204), c)).ok
        assert not (
            await probe_http(spec(kind="http", url="http://x.test/", expect_status=200), c)
        ).ok


async def test_http_redirect_loop_is_capped():
    from netwatch.probes import Prober

    loop = httpx.MockTransport(lambda req: httpx.Response(302, headers={"Location": "/again"}))
    p = Prober()
    p._http = httpx.AsyncClient(transport=loop, follow_redirects=True, max_redirects=5)
    r = await probe_http(spec(kind="http", url="http://x.test/"), p.http)
    assert not r.ok and r.error == "too_many_redirects"
    await p.aclose()
    assert Prober().http.max_redirects == 5


async def test_http_connect_error():
    def boom(_req):
        raise httpx.ConnectError("nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as c:
        r = await probe_http(spec(kind="http", url="http://x.test/"), c)
    assert not r.ok and r.error == "connect"


# ── DNS ──────────────────────────────────────────────────────────────────────


class _DnsServer(asyncio.DatagramProtocol):
    def __init__(self, rcode=0, answer=True):
        self.rcode, self.answer = rcode, answer

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        q = dns.message.from_wire(data)
        resp = dns.message.make_response(q)
        resp.set_rcode(self.rcode)
        if self.answer and self.rcode == 0:
            resp.answer.append(dns.rrset.from_text(q.question[0].name, 60, "IN", "A", "192.0.2.10"))
        self.transport.sendto(resp.to_wire(), addr)


async def _serve_dns(**kw):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _DnsServer(**kw), local_addr=("127.0.0.1", 0)
    )
    return transport, transport.get_extra_info("sockname")[1]


@pytest.mark.parametrize(
    ("kw", "ok", "error"),
    [({}, True, None), ({"rcode": 2}, False, "servfail"), ({"answer": False}, False, "no_answer")],
)
async def test_dns_outcomes(kw, ok, error):
    transport, port = await _serve_dns(**kw)
    try:
        r = await probe_dns(spec(kind="dns", port=port, dns_query="example.com", dns_rdtype="A"))
    finally:
        transport.close()
    assert r.ok is ok and r.error == error
    assert r.rtt_ms is not None


async def test_dns_timeout():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))  # bound but never answers
    try:
        r = await probe_dns(spec(kind="dns", port=sock.getsockname()[1], timeout_s=0.3))
    finally:
        sock.close()
    assert not r.ok and r.error == "timeout"


# ── ICMP (icmplib mocked; raw sockets are unavailable in CI) ─────────────────


class _Host:
    def __init__(self, alive, rtts):
        self.is_alive, self.rtts = alive, rtts


async def test_icmp_reply(monkeypatch):
    async def fake(host, **kw):
        assert kw["count"] == 1 and kw["privileged"] is True
        return _Host(True, [3.25])

    monkeypatch.setattr(icmp_mod, "async_ping", fake)
    r = await icmp_mod.probe_icmp(spec())
    assert r.ok and r.rtt_ms == 3.25


async def test_icmp_timeout_and_permission(monkeypatch):
    async def lost(host, **kw):
        return _Host(False, [])

    monkeypatch.setattr(icmp_mod, "async_ping", lost)
    assert (await icmp_mod.probe_icmp(spec())).error == "timeout"

    async def denied(host, **kw):
        raise icmp_mod.SocketPermissionError(True)

    monkeypatch.setattr(icmp_mod, "async_ping", denied)
    assert (await icmp_mod.probe_icmp(spec())).error == "permission"


async def test_prober_never_raises(monkeypatch):
    async def crash(host, **kw):
        raise RuntimeError("bug")

    monkeypatch.setattr(icmp_mod, "async_ping", crash)
    p = Prober()
    r = await p.run(spec())
    assert not r.ok and r.error == "internal"
    assert (await p.run(spec(kind="bogus"))).error == "unknown_kind"
    await p.aclose()
