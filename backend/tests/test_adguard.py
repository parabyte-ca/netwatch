from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from netwatch.services.adguard.client import (
    AdGuardClient,
    AdGuardError,
    AdGuardLoginError,
    parse_time,
    redact,
)
from netwatch.services.adguard.service import AdGuardStore
from netwatch.services.events import DNS, KINDS

USER, PASSWORD = "netwatch", "adguard-pass-123"
HOST, PORT = "192.168.2.53", 80


def iso(dt: datetime) -> str:
    """AdGuard writes nanoseconds and a zone offset (here Eastern daylight time)."""
    local = dt.astimezone(timezone(timedelta(hours=-4)))
    return local.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123-04:00"


class FakeAdGuard:
    """Serves AdGuard Home's /control API and checks basic auth on every request."""

    def __init__(self) -> None:
        self.protection = True
        self.querylog_on = True
        self.entries: list[dict] = []  # newest first, like AdGuard
        self.clients = {"clients": [{"name": "Office PC", "ids": ["192.168.2.20"]}],
                        "auto_clients": [{"ip": "192.168.2.30", "name": "tv.lan"}]}  # fmt: skip
        self.stats = {
            "num_dns_queries": 1000, "num_blocked_filtering": 150, "avg_processing_time": 0.012,
            "top_clients": [{"192.168.2.20": 600}, {"192.168.2.30": 400}],
            "top_upstreams_responses": [{"tls://1.1.1.1:853": 700}, {"8.8.8.8:53": 150}],
            "top_upstreams_avg_time": [{"tls://1.1.1.1:853": 0.015}, {"8.8.8.8:53": 0.020}],
        }  # fmt: skip
        self.fail: int | None = None  # HTTP status to answer with
        self.requests: list[httpx.Request] = []

    def query(
        self,
        ts: datetime,
        *,
        client="192.168.2.20",
        upstream="tls://1.1.1.1:853",
        status="NOERROR",
        reason="NotFilteredNotFound",
        ms=12.0,
        name="example.com",
    ) -> None:
        self.entries.insert(0, {
            "time": iso(ts), "client": client, "upstream": upstream, "status": status,
            "reason": reason, "elapsedMs": str(ms), "question": {"name": name, "type": "A"},
        })  # fmt: skip

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        if req.headers.get("authorization") != f"Basic {token}":
            return httpx.Response(401, text="Unauthorized")
        if self.fail:
            return httpx.Response(self.fail)
        assert req.method == "GET"  # read-only
        path = req.url.path
        if path == "/control/status":
            return httpx.Response(200, json={
                "version": "v0.107.55", "running": True, "protection_enabled": self.protection,
                "dhcp_available": True, "dns_port": 53})  # fmt: skip
        if path == "/control/stats":
            return httpx.Response(200, json=self.stats)
        if path == "/control/querylog/config":
            return httpx.Response(200, json={"enabled": self.querylog_on, "interval": 86400000})
        if path == "/control/querylog":
            data = self.entries if self.querylog_on else []
            older = req.url.params.get("older_than")
            if older:
                cut = parse_time(older)
                data = [e for e in data if parse_time(e["time"]) < cut]
            page = data[: int(req.url.params.get("limit", 500))]
            return httpx.Response(
                200, json={"data": page, "oldest": page[-1]["time"] if page else ""}
            )
        if path == "/control/clients":
            return httpx.Response(200, json={**self.clients, "supported_tags": []})
        return httpx.Response(404)


def client_for(fake, password=PASSWORD) -> AdGuardClient:
    return AdGuardClient(HOST, PORT, USER, password, transport=httpx.MockTransport(fake))


# ── client ────────────────────────────────────────────────────────────────────


async def test_status_stats_and_clients():
    fake = FakeAdGuard()
    c = client_for(fake)
    s = await c.status()
    assert s["protection"] is True and s["version"] == "v0.107.55" and s["dhcp_available"]
    assert (await c.stats())["num_dns_queries"] == 1000
    ids = {x["id"]: x["name"] for x in await c.clients()}
    assert ids == {"192.168.2.20": "Office PC", "192.168.2.30": "tv.lan"}
    assert all(r.method == "GET" for r in fake.requests)
    await c.aclose()


async def test_querylog_pages_back_to_the_cursor():
    fake = FakeAdGuard()
    t0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    for i in range(1200):
        fake.query(t0 + timedelta(milliseconds=50 * i))
    c = client_for(fake)
    rows, cut = await c.querylog(t0 + timedelta(milliseconds=50 * 199))
    assert len(rows) == 1000 and not cut  # entries 200..1199, over three pages
    assert rows[0]["_ts"] > rows[-1]["_ts"]
    rows, cut = await c.querylog(None, limit=300)
    assert len(rows) == 300 and cut
    await c.aclose()


async def test_errors_are_safe_to_show():
    c = client_for(FakeAdGuard(), "wrong")
    with pytest.raises(AdGuardLoginError, match="wrong username or password"):
        await c.status()
    await c.aclose()
    fake = FakeAdGuard()
    fake.fail = 502
    c = client_for(fake)
    with pytest.raises(AdGuardError, match="HTTP 502"):
        await c.stats()
    await c.aclose()

    def boom(req):
        raise httpx.ConnectError("connection refused", request=req)

    c = AdGuardClient(HOST, PORT, USER, PASSWORD, transport=httpx.MockTransport(boom))
    with pytest.raises(AdGuardError) as e:
        await c.status()
    assert PASSWORD not in str(e.value) and "cannot reach" in str(e.value)
    await c.aclose()

    def not_adguard(req):
        return httpx.Response(200, json={"hello": "world"})

    c = AdGuardClient(HOST, PORT, USER, PASSWORD, transport=httpx.MockTransport(not_adguard))
    with pytest.raises(AdGuardError, match="is this AdGuard Home"):
        await c.status()
    await c.aclose()


def test_redact_and_time_parsing():
    assert redact({"users": [{"name": "a", "password": "x"}], "api_key": "k"}) == {
        "users": [{"name": "a", "password": "••••"}],
        "api_key": "••••",
    }
    t = parse_time("2026-11-14T08:00:00.123456789-04:00")
    assert t == datetime(2026, 11, 14, 12, 0, 0, 123456, tzinfo=UTC)
    assert parse_time("garbage") is None and parse_time(None) is None


def test_store_roundtrip_and_purge(store):
    s = AdGuardStore(store.engine)
    s.add({"ts_ms": 1000, "reachable": True, "protection": True, "queries": 10, "blocked": 2,
           "servfail": 0, "errors": 0, "avg_ms": 12.5, "p95_ms": 30.0, "source": "querylog",
           "upstreams": {"tls://1.1.1.1": {"n": 8, "avg_ms": 14.0, "fail": 0}},
           "details": {"top_clients": [["192.168.2.20", 6]]}})  # fmt: skip
    s.add({"ts_ms": 2000, "reachable": False, "source": "stats"})
    rows = s.history(0, 10_000)
    assert [r["ts_ms"] for r in rows] == [1000, 2000]
    assert rows[0]["upstreams"]["tls://1.1.1.1"]["n"] == 8
    assert rows[1]["upstreams"] == {} and rows[1]["protection"] is None
    assert s.latest()["ts_ms"] == 2000
    assert s.purge(1500) == 1 and s.history(0, 10_000)[0]["ts_ms"] == 2000
    assert DNS == "dns" and DNS in KINDS
