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
        self.entries.append({
            "time": iso(ts), "client": client, "upstream": upstream, "status": status,
            "reason": reason, "elapsedMs": str(ms), "question": {"name": name, "type": "A"},
        })  # fmt: skip
        self.entries.sort(key=lambda e: parse_time(e["time"]), reverse=True)  # newest first

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


# ── service ───────────────────────────────────────────────────────────────────

T = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
T_MS = int(T.timestamp() * 1000)


def at(seconds: float) -> datetime:
    return T + timedelta(seconds=seconds)


def ms(seconds: float) -> int:
    return T_MS + int(seconds * 1000)


@pytest.fixture
def svc(store, tmp_path):
    from netwatch.services.adguard.service import AdGuardService
    from netwatch.services.events import EventLog
    from netwatch.services.secrets import SecretBox

    fake = FakeAdGuard()
    s = AdGuardService(
        store,
        EventLog(store.engine),
        AdGuardStore(store.engine),
        SecretBox(tmp_path / "data"),
        client_factory=lambda cfg, pw: client_for(fake, pw),
    )
    s.save(HOST, PORT, False, USER, True, PASSWORD)
    s.fake = fake
    return s


def dns_events(svc, typ=None):
    evs = svc.events.list(0, 10**13, kinds=[DNS])
    return [e for e in evs if typ is None or e["details"].get("type") == typ]


async def test_interval_metrics_from_the_query_log(svc):
    f = svc.fake
    for i in range(6):
        f.query(at(-40 + i), ms=10.0 + i)
    f.query(at(-30), upstream="8.8.8.8:53", ms=40.0)
    f.query(at(-29), upstream="8.8.8.8:53", status="SERVFAIL", ms=900.0)
    f.query(at(-28), reason="FilteredBlackList", upstream="", ms=0.2, client="192.168.2.30")
    f.query(at(-27), reason="FilteredBlackList", upstream="", ms=0.1, client="192.168.2.30")
    f.query(at(-200))  # before the first interval: ignored
    assert await svc.poll(T_MS)
    row = svc.adguard_store.latest()
    assert row["source"] == "querylog" and row["reachable"] and row["protection"]
    assert (row["queries"], row["blocked"], row["servfail"], row["errors"]) == (10, 2, 1, 0)
    assert row["upstreams"]["8.8.8.8:53"] == {"n": 2, "fail": 1, "avg_ms": 470.0}
    assert row["upstreams"]["tls://1.1.1.1:853"]["n"] == 6
    assert row["p95_ms"] == 900.0 and row["details"]["top_clients"][0] == ["192.168.2.20", 8]
    # The next poll counts only what arrived since.
    f.query(at(20))
    f.query(at(25), status="REFUSED")
    assert await svc.poll(ms(60))
    row = svc.adguard_store.latest()
    assert (row["queries"], row["errors"]) == (2, 1)


async def test_stats_fallback_when_the_query_log_is_off(svc):
    f = svc.fake
    f.querylog_on = False
    assert await svc.poll(T_MS)
    assert svc.adguard_store.latest()["queries"] is None  # no baseline yet
    f.stats = {**f.stats, "num_dns_queries": 1050, "num_blocked_filtering": 160}
    assert await svc.poll(ms(60))
    row = svc.adguard_store.latest()
    assert row["source"] == "stats" and (row["queries"], row["blocked"]) == (50, 10)
    assert row["avg_ms"] == 12.0 and row["upstreams"]["8.8.8.8:53"]["avg_ms"] == 20.0
    f.stats = {**f.stats, "num_dns_queries": 900}  # the rolling window moved on
    assert await svc.poll(ms(120))
    assert svc.adguard_store.latest()["queries"] is None


async def test_protection_and_upstream_events(svc):
    f = svc.fake
    await svc.poll(T_MS)
    f.protection = False
    await svc.poll(ms(60))
    (prot,) = dns_events(svc, "protection_disabled")
    assert prot["open"] and "protection is off" in prot["title"]
    f.protection = True
    for i in range(12):
        f.query(at(70 + i), upstream="8.8.8.8:53", status="SERVFAIL" if i < 8 else "NOERROR")
    await svc.poll(ms(120))
    assert not dns_events(svc, "protection_disabled")[0]["open"]
    (up,) = dns_events(svc, "upstream_failing")
    assert up["open"] and up["details"]["upstream"] == "8.8.8.8:53"
    assert "8 of 12" in up["title"]
    for k in range(3):  # three clean intervals close it
        assert dns_events(svc, "upstream_failing")[0]["open"]
        await svc.poll(ms(180 + 60 * k))
    assert not dns_events(svc, "upstream_failing")[0]["open"]


async def test_error_spike_and_latency_spike_have_cooldowns(svc):
    f = svc.fake
    await svc.poll(T_MS)
    for i in range(20):
        f.query(at(10 + i * 0.5), status="SERVFAIL" if i < 3 else "NOERROR")
    await svc.poll(ms(60))
    (err,) = dns_events(svc, "dns_errors")
    assert "3 of 20" in err["title"] and err["details"]["servfail"] == 3
    for i in range(20):
        f.query(at(70 + i * 0.5), status="SERVFAIL")
    await svc.poll(ms(120))
    assert len(dns_events(svc, "dns_errors")) == 1  # within the 30 min cooldown
    # Latency: 30 normal intervals, then a slow one.
    for k in range(30):
        f.query(at(130 + 60 * k), ms=12.0)
        await svc.poll(ms(180 + 60 * k))
    f.query(at(1990), ms=600.0)
    await svc.poll(ms(2000))
    (spike,) = dns_events(svc, "latency_spike")
    assert "600 ms" in spike["title"] and spike["details"]["median_ms"] == 12.0
    f.query(at(2050), ms=700.0)
    await svc.poll(ms(2060))
    assert len(dns_events(svc, "latency_spike")) == 1


async def test_new_clients_after_the_first_poll(svc, store):
    from netwatch.services.discovery.inventory import DeviceStore

    f = svc.fake
    f.query(at(-10))
    await svc.poll(T_MS)  # baseline: Office PC, tv.lan and the querying client
    assert dns_events(svc, "new_client") == []
    devs = DeviceStore(store.engine)
    did = devs.insert({"mac": "aa:bb:cc:00:00:44", "first_seen_ms": 1, "last_seen_ms": 1})
    devs.upsert_ip(did, "192.168.2.44", 1, "arp")
    f.query(at(10), client="192.168.2.99")
    f.query(at(11), client="192.168.2.44")  # a device NetWatch already knows
    await svc.poll(ms(60))
    (ev,) = dns_events(svc, "new_client")
    assert ev["title"] == "New DNS client: 192.168.2.99" and ev["severity"] == "info"
    f.query(at(70), client="192.168.2.99")
    await svc.poll(ms(120))
    assert len(dns_events(svc, "new_client")) == 1


async def test_unreachable_opens_after_three_failures_and_closes(svc):
    f = svc.fake
    await svc.poll(T_MS)
    f.fail = 502
    for k in range(1, 3):
        assert not await svc.poll(ms(60 * k))
    assert dns_events(svc, "adguard_unreachable") == []
    await svc.poll(ms(180))
    (ev,) = dns_events(svc, "adguard_unreachable")
    assert ev["open"] and ev["start_ms"] == ms(60) and ev["severity"] == "critical"
    assert svc.adguard_store.latest()["reachable"] is False
    f.fail = None
    assert await svc.poll(ms(240))
    assert dns_events(svc, "adguard_unreachable")[0]["end_ms"] == ms(240)


async def test_sign_in_failures_pause_polling(svc):
    svc.save(HOST, PORT, False, USER, True, "wrong-password")
    for _ in range(3):
        assert not await svc.poll()
    assert svc.paused == "3 failed sign-ins" and "wrong username" in svc.error
    svc.save(HOST, PORT, False, USER, True, PASSWORD)
    assert svc.paused is None and await svc.poll()


def test_password_only_goes_to_its_address_and_disabling_closes_events(svc):
    assert svc.configured and svc.password() == PASSWORD
    svc.save(HOST, 3000, False, USER, True, None)  # new port, no password given
    assert svc.password() is None and not svc.configured
    svc.save(HOST, PORT, False, USER, True, PASSWORD)
    eid = svc.events.open(DNS, "warning", 1, "x", details={"type": "protection_disabled"})
    svc._open["protection"] = eid
    svc.save(HOST, PORT, False, USER, False, None)
    assert svc.events.get(eid)["end_ms"] is not None
    assert PASSWORD not in str(svc.store.kv_get("adguard_password"))


# ── admin API ─────────────────────────────────────────────────────────────────


def body(**over) -> dict:
    return {"host": HOST, "port": PORT, "https": False, "username": USER, "enabled": True,
            "password": PASSWORD, **over}  # fmt: skip


def test_admin_api(client):
    from netwatch.services.events import SECURITY

    st = client.app.state
    fake = FakeAdGuard()
    st.adguard.client_factory = lambda cfg, pw: client_for(fake, pw)
    assert client.get("/api/admin/adguard").json()["password_set"] is False
    assert client.put("/api/admin/adguard", json=body(host="8.8.8.8")).status_code == 422
    assert client.put("/api/admin/adguard", json=body(username="")).status_code == 422
    assert client.put("/api/admin/adguard", json=body(port=0)).status_code == 422
    r = client.post("/api/admin/adguard/test", json=body())
    assert r.status_code == 422  # extra field "enabled" refused
    test_body = {k: v for k, v in body().items() if k != "enabled"}
    r = client.post("/api/admin/adguard/test", json=test_body).json()
    assert r == {"ok": True, "version": "v0.107.55", "protection": True, "queries_24h": 1000,
                 "querylog": True}  # fmt: skip
    r = client.post("/api/admin/adguard/test", json={**test_body, "password": "nope"}).json()
    assert r["ok"] is False and "wrong username" in r["error"]
    r = client.put("/api/admin/adguard", json=body())
    assert r.status_code == 200 and r.json()["password_set"] and PASSWORD not in r.text
    assert PASSWORD not in client.get("/api/admin/adguard").text
    log = st.events.list(0, 10**13, kinds=[SECURITY])
    assert any("AdGuard Home integration changed" in e["title"] and "password saved" in e["title"]
               for e in log)  # fmt: skip
    assert all(PASSWORD not in str(e) for e in log)
    # The saved password is used for the saved address only.
    no_pw = {k: v for k, v in test_body.items() if k != "password"}
    assert client.post("/api/admin/adguard/test", json=no_pw).json()["ok"]
    for other in ({"host": "192.168.2.54"}, {"port": 3000}, {"https": True}, {"username": "x"}):
        r = client.post("/api/admin/adguard/test", json={**no_pw, **other})
        assert r.status_code == 422 and "password" in r.text
    client.portal.call(st.adguard.poll)
    assert client.get("/api/admin/adguard").json()["latest"]["reachable"] is True
