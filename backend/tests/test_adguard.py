from __future__ import annotations

import base64
import json
import time
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
        self.dhcp: dict | None = None  # None: no DHCP support (404)
        self.refuse_lease = 0  # refuse this many add_static_lease calls
        self.blocked_global: list[str] = []  # apps blocked for everyone
        self.services = [{"id": "youtube", "name": "YouTube"},
                         {"id": "tiktok", "name": "TikTok"},
                         {"id": "roblox", "name": "Roblox"},
                         {"id": "discord", "name": "Discord"}]  # fmt: skip
        self.safebrowsing = False
        self.parental = False
        self.safesearch = {"enabled": False, "bing": True, "duckduckgo": True, "google": True,
                           "pixabay": True, "yandex": True, "youtube": True}  # fmt: skip
        self.user_rules = ["||doubleclick.net^", "# my own rule"]
        self.upstream_dns = ["tls://1.1.1.1", "tls://1.0.0.1"]
        self.bad_upstreams: set[str] = set()  # fail AdGuard's upstream test
        self.honour_duration = True  # older AdGuard ignores the pause duration
        self.paused_until: float | None = None
        self.posts: list[tuple[str, dict]] = []
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
        path = req.url.path
        if req.method == "POST":
            # Only the action endpoints may ever be written to.
            assert path in WRITE_PATHS, f"unexpected write to {path}"
            body = json.loads(req.content or b"{}")
            self.posts.append((path, body))
            return self.write(path, body)
        assert req.method == "GET"  # read-only everywhere else
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
        if path == "/control/filtering/status":
            return httpx.Response(200, json={"enabled": True, "user_rules": self.user_rules})
        if path == "/control/blocked_services/all":
            return httpx.Response(200, json={"blocked_services": [
                {**x, "icon_svg": "", "rules": []} for x in self.services]})  # fmt: skip
        if path == "/control/blocked_services/get":
            return httpx.Response(200, json={"ids": self.blocked_global, "schedule": {}})
        if path == "/control/safebrowsing/status":
            return httpx.Response(200, json={"enabled": self.safebrowsing})
        if path == "/control/parental/status":
            return httpx.Response(200, json={"enabled": self.parental, "sensitivity": 13})
        if path == "/control/safesearch/status":
            return httpx.Response(200, json=self.safesearch)
        if path == "/control/dns_info":
            return httpx.Response(200, json={"upstream_dns": self.upstream_dns,
                                             "bootstrap_dns": ["9.9.9.10"]})  # fmt: skip
        if path == "/control/dhcp/status" and self.dhcp is not None:
            return httpx.Response(200, json=self.dhcp)
        return httpx.Response(404)


WRITE_PATHS = {"/control/protection", "/control/filtering/set_rules", "/control/dns_config",
               "/control/test_upstream_dns", "/control/dhcp/add_static_lease",
               "/control/dhcp/remove_static_lease", "/control/clients/add",
               "/control/clients/update", "/control/clients/delete"}  # fmt: skip


def _write(self, path: str, body: dict) -> httpx.Response:
    if path == "/control/protection":
        self.protection = bool(body["enabled"])
        self.paused_until = (
            None
            if body["enabled"] or "duration" not in body or not self.honour_duration
            else body["duration"]
        )
        return httpx.Response(200, text="OK")
    if path == "/control/filtering/set_rules":
        self.user_rules = list(body["rules"])
        return httpx.Response(200)
    if path in ("/control/clients/add", "/control/clients/update", "/control/clients/delete"):
        items = self.clients.setdefault("clients", [])
        if path.endswith("/delete"):
            before = len(items)
            items[:] = [c for c in items if c["name"] != body["name"]]
            return httpx.Response(200 if len(items) < before else 400, text="client not found\n")
        data, old = (body["data"], body["name"]) if path.endswith("/update") else (body, None)
        if old is not None and not any(c["name"] == old for c in items):
            return httpx.Response(400, text="client not found\n")
        others = [c for c in items if c["name"] != old]
        if any(c["name"] == data["name"] for c in others):
            return httpx.Response(400, text=f"client {data['name']!r} already exists\n")
        used = {i for c in others for i in c.get("ids") or []}
        if any(i in used for i in data["ids"]):
            return httpx.Response(400, text="another client uses the same id\n")
        items[:] = [*others, dict(data)]
        return httpx.Response(200)
    if path == "/control/dhcp/add_static_lease":
        statics = self.dhcp.setdefault("static_leases", [])
        if self.refuse_lease:
            self.refuse_lease -= 1
            return httpx.Response(400, text="invalid static lease\n")
        if any(x["mac"] == body["mac"] or x["ip"] == body["ip"] for x in statics):
            return httpx.Response(400, text="static lease already exists\n")
        statics.append(dict(body))
        return httpx.Response(200)
    if path == "/control/dhcp/remove_static_lease":
        statics = self.dhcp.get("static_leases") or []
        if body not in statics:
            return httpx.Response(400, text="lease not found\n")
        statics.remove(body)
        return httpx.Response(200)
    if path == "/control/test_upstream_dns":
        return httpx.Response(200, json={
            u: (f"couldn't communicate with upstream: {u}" if u in self.bad_upstreams else "OK")
            for u in body["upstream_dns"]})  # fmt: skip
    if path == "/control/dns_config":
        if any(u in self.bad_upstreams for u in body["upstream_dns"]):
            return httpx.Response(400, text="validating upstream servers: bad upstream\n")
        self.upstream_dns = list(body["upstream_dns"])
        return httpx.Response(200)
    return httpx.Response(404)


FakeAdGuard.write = _write


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


async def test_clients_are_grouped_and_ids_classified():
    fake = FakeAdGuard()
    fake.clients = {
        "clients": [{"name": "Laptop", "ids": ["AA-BB-CC-DD-EE-01", "192.168.2.40"]},
                    {"name": "Kids", "ids": ["192.168.2.0/28", "kid-tablet"]}],
        "auto_clients": [{"ip": "192.168.2.30", "name": ""}, {"ip": "fe80::1"}],
    }  # fmt: skip
    c = client_for(fake)
    got = await c.clients()
    assert got[0] == {"id": "aa:bb:cc:dd:ee:01", "kind": "mac", "name": "Laptop", "group": 0}
    assert got[1] == {"id": "192.168.2.40", "kind": "ip", "name": "Laptop", "group": 0}
    assert [g["kind"] for g in got[2:4]] == ["other", "other"]
    assert got[4] == {"id": "192.168.2.30", "kind": "ip", "name": None, "group": None}
    assert got[5]["kind"] == "other"
    await c.aclose()


async def test_dhcp_status_leases_or_not_available():
    fake = FakeAdGuard()
    c = client_for(fake)
    assert await c.dhcp_status() == {"available": False, "enabled": False, "leases": []}
    fake.dhcp = {
        "enabled": True, "interface_name": "eth0",
        "leases": [{"mac": "AA:BB:CC:DD:EE:02", "ip": "192.168.2.101", "hostname": "pixel-9",
                    "expires": "2026-11-15T12:00:00Z"},
                   {"mac": "not-a-mac", "ip": "192.168.2.102", "hostname": "x"}],
        "static_leases": [{"mac": "aa-bb-cc-dd-ee-03", "ip": "192.168.2.15", "hostname": ""}],
    }  # fmt: skip
    d = await c.dhcp_status()
    assert d["available"] and d["enabled"]
    assert d["leases"] == [
        {"mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.2.101", "hostname": "pixel-9", "static": False},
        {"mac": "aa:bb:cc:dd:ee:03", "ip": "192.168.2.15", "hostname": None, "static": True},
    ]
    fake.dhcp["enabled"] = False
    assert (await c.dhcp_status())["enabled"] is False
    fake.fail = 401
    with pytest.raises(AdGuardLoginError):
        await c.dhcp_status()
    await c.aclose()


async def test_write_methods_touch_only_their_endpoints():
    fake = FakeAdGuard()
    c = client_for(fake)
    await c.set_protection(False, 1800)
    assert fake.posts[-1] == ("/control/protection", {"enabled": False, "duration": 1_800_000})
    assert fake.protection is False
    await c.set_protection(True)
    assert fake.posts[-1] == ("/control/protection", {"enabled": True}) and fake.protection
    assert await c.user_rules() == ["||doubleclick.net^", "# my own rule"]
    await c.set_user_rules(["||x.com^"])
    assert fake.user_rules == ["||x.com^"]
    fake.user_rules = None  # a fresh AdGuard Home with no custom rules answers null
    assert await c.user_rules() == []
    await c.set_user_rules(["@@||example.org^"])
    assert fake.user_rules == ["@@||example.org^"]
    assert (await c.upstreams())["upstream_dns"] == ["tls://1.1.1.1", "tls://1.0.0.1"]
    fake.bad_upstreams = {"udp://10.9.9.9"}
    r = await c.test_upstreams(["tls://9.9.9.9", "udp://10.9.9.9"], ["9.9.9.10"])
    assert r["tls://9.9.9.9"] == "OK" and r["udp://10.9.9.9"].startswith("couldn't")
    with pytest.raises(AdGuardError, match="refused the change \\(HTTP 400\\): validating"):
        await c.set_upstreams(["udp://10.9.9.9"])
    fake.fail = 401
    with pytest.raises(AdGuardLoginError):
        await c.set_protection(True)
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


def test_client_store_hours_summary_purge_and_strip(store):
    from netwatch.services.adguard.clients import HOUR_MS, ClientStore, hour_of
    from netwatch.services.discovery.inventory import DeviceStore

    devs = DeviceStore(store.engine)
    d1 = devs.insert({"mac": "aa:bb:cc:00:00:01", "first_seen_ms": 0, "last_seen_ms": 0})
    cs = ClientStore(store.engine)
    h0, h1 = 10 * HOUR_MS, 11 * HOUR_MS
    assert hour_of(h1 + 1234) == h1
    cs.put([
        {"hour_ms": h0, "client": "192.168.2.20", "device_id": d1, "queries": 100, "blocked": 10,
         "failed": 2, "domains": {"a.com": 50, "b.com": 20}, "blocked_domains": {"ads.x": 10}},
        {"hour_ms": h0, "client": "192.168.2.99", "device_id": None, "queries": 7, "blocked": 0,
         "failed": 0, "domains": {"a.com": 7}, "blocked_domains": {}},
    ])  # fmt: skip
    # The current hour is rewritten every poll.
    row = {"hour_ms": h1, "client": "192.168.2.20", "device_id": d1, "queries": 5, "blocked": 0,
           "failed": 0, "domains": {"b.com": 5}, "blocked_domains": {}}  # fmt: skip
    cs.put([row])
    cs.put([{**row, "queries": 40, "domains": {"b.com": 40}}])
    assert cs.hour(h1)[0]["queries"] == 40 and cs.hour(h1)[0]["domains"] == {"b.com": 40}
    s = cs.summary([d1], h0, h1 + HOUR_MS)
    assert s["queries"] == 140 and s["blocked_pct"] == round(1000 / 140, 1)
    assert [h["queries"] for h in s["hourly"]] == [100, 40]
    assert s["top_domains"][0] == {"domain": "b.com", "queries": 60}
    assert s["top_blocked"] == [{"domain": "ads.x", "queries": 10}]
    n = cs.network(h0, h1 + HOUR_MS)
    assert n["unknown_clients"] == [{"client": "192.168.2.99", "queries": 7}]
    assert n["top_domains"][0]["domain"] == "b.com"
    assert sorted(cs.device_hours(0, h1 + HOUR_MS)[d1]) == [(40, 0), (100, 10)]
    # After 7 days the names go, the counts stay; after the rollup retention the rows go.
    assert cs.strip_domains(h1) == 2
    s = cs.summary([d1], h0, h1 + HOUR_MS)
    assert s["queries"] == 140 and s["top_domains"] == [{"domain": "b.com", "queries": 40}]
    assert cs.strip_domains(h1) == 0
    assert cs.purge(h1) == 2 and cs.summary([d1], 0, h1 + HOUR_MS)["queries"] == 40
    # Deleting the device keeps its history as an unknown client.
    devs.delete(d1)
    assert cs.hour(h1)[0]["device_id"] is None


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


@pytest.fixture
def isvc(store, tmp_path):
    """The service with the per-client store and a device inventory (Phase 2)."""
    from netwatch.services.adguard.clients import ClientStore
    from netwatch.services.adguard.service import AdGuardService
    from netwatch.services.discovery.inventory import DeviceStore, Inventory
    from netwatch.services.events import EventLog
    from netwatch.services.secrets import SecretBox

    fake = FakeAdGuard()

    def make():
        s = AdGuardService(
            store,
            EventLog(store.engine),
            AdGuardStore(store.engine),
            SecretBox(tmp_path / "data"),
            client_factory=lambda cfg, pw: client_for(fake, pw),
            clients=ClientStore(store.engine),
            inventory=Inventory(DeviceStore(store.engine)),
        )
        s.save(HOST, PORT, False, USER, True, PASSWORD)
        s.fake, s.make = fake, make
        return s

    return make()


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
    assert client.get("/api/admin/adguard").json()["state"] == "off"
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


def test_report_has_a_dns_section(client):
    import time as _time

    st = client.app.state
    fake = FakeAdGuard()
    st.adguard.client_factory = lambda cfg, pw: client_for(fake, pw)
    st.adguard.save(HOST, PORT, False, USER, True, PASSWORD)
    now = datetime.now(UTC)
    for i in range(10):
        fake.query(now - timedelta(seconds=30 - i), ms=20.0,
                   reason="FilteredBlackList" if i < 2 else "NotFilteredNotFound")  # fmt: skip
    assert client.portal.call(st.adguard.poll)
    start = int(_time.time()) - 3600
    r = client.get(f"/api/report?start={start}").json()
    assert r["dns"]["queries"] == 10 and r["dns"]["blocked_pct"] == 20.0
    assert r["dns"]["unreachable"] == 0
    assert "AdGuard Home stayed reachable" in " ".join(r["ruled_out"])
    assert "## DNS (AdGuard Home)" in r["markdown"]


def test_dns_page_summary(client):
    st = client.app.state
    fake = FakeAdGuard()
    st.adguard.client_factory = lambda cfg, pw: client_for(fake, pw)
    st.adguard.save(HOST, PORT, False, USER, True, PASSWORD)
    r = client.get("/api/adguard").json()
    assert r["series"] == [] and r["queries"] == 0 and r["blocked_pct"] is None
    assert "password" not in r
    now = datetime.now(UTC)
    for i in range(10):
        fake.query(now - timedelta(seconds=30 - i), ms=10.0 + i, client="192.168.1.50",
                   reason="FilteredBlackList" if i < 3 else "NotFilteredNotFound")  # fmt: skip
    assert client.portal.call(st.adguard.poll)
    r = client.get("/api/adguard?hours=6").json()
    assert r["hours"] == 6 and len(r["series"]) == 1
    assert r["queries"] == 10 and r["blocked_pct"] == 30.0 and r["failed_pct"] == 0.0
    assert r["upstreams"] and r["upstreams"][0]["queries"] == 7
    assert r["top_clients"][0]["client"] == "192.168.1.50"
    assert r["top_clients"][0]["queries"] == 10
    assert client.get("/api/adguard?hours=0").status_code == 422
    assert PASSWORD not in json.dumps(r)


async def test_queries_per_client_and_hour_survive_a_restart(isvc):
    f = isvc.fake
    dev = isvc.inventory.store.insert({"mac": "aa:bb:cc:00:00:20", "first_seen_ms": 0,
                                       "last_seen_ms": 0, "last_ip": "192.168.2.20"})  # fmt: skip
    isvc.inventory.store.upsert_ip(dev, "192.168.2.20", T_MS - 60_000, "arp")
    for i in range(5):
        f.query(at(-40 + i), name="Example.COM.")
    f.query(at(-30), name="ads.tracker.net", reason="FilteredBlackList", upstream="")
    f.query(at(-29), name="broken.example", status="SERVFAIL")
    f.query(at(-28), client="192.168.2.99", name="example.com")
    assert await isvc.poll(T_MS)
    prev = T_MS - 3_600_000  # T is on the hour, so these entries belong to the hour before
    rows = {r["client"]: r for r in isvc.client_store.hour(prev)}
    r = rows["192.168.2.20"]
    assert (r["device_id"], r["queries"], r["blocked"], r["failed"]) == (dev, 7, 1, 1)
    assert r["domains"] == {"example.com": 5, "broken.example": 1}
    assert r["blocked_domains"] == {"ads.tracker.net": 1}
    assert rows["192.168.2.99"]["device_id"] is None
    # A restart carries on counting the same hour instead of starting from zero.
    again = isvc.make()
    f.query(at(-10), name="example.com")  # late entry for the previous hour
    f.query(at(30), name="example.com")
    assert await again.poll(ms(60))
    r = {r["client"]: r for r in again.client_store.hour(prev)}["192.168.2.20"]
    assert r["queries"] == 8 and r["domains"]["example.com"] == 6
    assert again.client_store.hour(T_MS)[0]["queries"] == 1
    s = again.client_store.summary([dev], T_MS - 3_600_000, T_MS + 3_600_000)
    assert s["queries"] == 9 and s["top_blocked"][0]["domain"] == "ads.tracker.net"


async def test_clients_and_leases_feed_the_inventory(isvc):
    f = isvc.fake
    inv = isvc.inventory
    tv = inv.store.insert({"mac": "aa:bb:cc:00:00:30", "first_seen_ms": 0, "last_seen_ms": 0,
                           "last_ip": "192.168.2.30"})  # fmt: skip
    f.clients = {
        "clients": [{"name": "Alan's MacBook Air", "ids": ["AA:BB:CC:00:00:40", "192.168.2.40"]},
                    {"name": "Living room TV", "ids": ["192.168.2.30"]},
                    {"name": "Nobody here", "ids": ["192.168.2.77"]}],
        "auto_clients": [{"ip": "192.168.2.88", "name": "printer.lan"}],
    }  # fmt: skip
    f.dhcp = {"enabled": True, "leases": [
        {"mac": "aa:bb:cc:00:00:40", "ip": "192.168.2.41", "hostname": "MacBook-Air"},
        {"mac": "aa:bb:cc:00:00:50", "ip": "192.168.2.50", "hostname": "pixel-9"}],
        "static_leases": []}  # fmt: skip
    isvc.fake.query(at(-20), client="192.168.2.50")
    assert await isvc.poll(T_MS)
    mac = inv.store.get_by_mac("aa:bb:cc:00:00:40")
    assert mac["details"]["adguard_name"] == "Alan's MacBook Air" and mac["type"] == "laptop"
    assert mac["last_ip"] == "192.168.2.41" and mac["hostname"] == "MacBook-Air"
    phone = inv.store.get_by_mac("aa:bb:cc:00:00:50")
    assert phone["hostname"] == "pixel-9" and phone["details"]["adguard_lease"]
    # An IP-only client names the device known at that address, and invents nothing.
    assert inv.store.get(tv)["details"]["adguard_name"] == "Living room TV"
    assert inv.store.with_ip("192.168.2.77") == [] and inv.store.with_ip("192.168.2.88") == []
    assert isvc.status()["dhcp"] == {"available": True, "enabled": True, "leases": 2}
    # Queries from a lease's address count for the device the lease just created.
    assert isvc.client_store.network(0, 10**13)["unknown_clients"] == []
    assert inv.store.count() == 3
    # Read again only every INVENTORY_EVERY polls; no DHCP support means no leases.
    reqs = len([r for r in f.requests if r.url.path == "/control/dhcp/status"])
    assert await isvc.poll(ms(60))
    assert len([r for r in f.requests if r.url.path == "/control/dhcp/status"]) == reqs


async def test_without_dhcp_support_leases_are_skipped(isvc):
    assert await isvc.poll(T_MS)
    assert isvc.status()["dhcp"] == {"available": False, "enabled": False, "leases": 0}
    assert isvc.inventory.store.count() == 0


def seed_history(isvc, dev, client, hours=29, q=100, b=5):
    from netwatch.services.adguard.clients import HOUR_MS

    isvc.client_store.put([
        {"hour_ms": T_MS - (i + 2) * HOUR_MS, "client": client, "device_id": dev, "queries": q,
         "blocked": b, "failed": 0, "domains": {}, "blocked_domains": {}}
        for i in range(hours)
    ])  # fmt: skip


def anomalies(isvc, typ):
    from netwatch.services.events import ANOMALY

    evs = isvc.events.list(0, 10**13, kinds=[ANOMALY])
    return [e for e in evs if e["details"].get("type") == typ]


def burst(f, n, client, start=-50.0, blocked=0):
    for i in range(n):
        f.query(at(start + i * 0.01), client=client,
                reason="FilteredBlackList" if i < blocked else "NotFilteredNotFound")  # fmt: skip


async def test_query_rate_alert_needs_history_and_cools_down(isvc):
    inv = isvc.inventory.store
    dev = inv.insert({"mac": "aa:bb:cc:00:00:60", "first_seen_ms": 0, "last_seen_ms": 0,
                      "name": "Garage camera", "last_ip": "192.168.2.60"})  # fmt: skip
    inv.upsert_ip(dev, "192.168.2.60", T_MS - 60_000, "arp")
    new = inv.insert({"mac": "aa:bb:cc:00:00:61", "first_seen_ms": 0, "last_seen_ms": 0,
                      "last_ip": "192.168.2.61"})  # fmt: skip
    inv.upsert_ip(new, "192.168.2.61", T_MS - 60_000, "arp")
    seed_history(isvc, dev, "192.168.2.60")
    seed_history(isvc, new, "192.168.2.61", hours=10)  # too little history to judge
    burst(isvc.fake, 600, "192.168.2.60")
    burst(isvc.fake, 600, "192.168.2.61", start=-45.0)
    assert await isvc.poll(T_MS)
    [e] = anomalies(isvc, "dns_query_rate")
    assert e["title"] == "Garage camera made 600 DNS queries in the last hour (usually about 100)"
    assert e["details"]["device_id"] == dev and e["details"]["usual_queries"] == 100
    assert e["severity"] == "warning" and "top domains" in e["details"]["hint"]
    # Still busy a minute later: no second alert inside the cooldown.
    burst(isvc.fake, 100, "192.168.2.60", start=20.0)
    assert await isvc.poll(ms(60))
    assert len(anomalies(isvc, "dns_query_rate")) == 1


async def test_blocked_rate_alert_only_when_unusual(isvc):
    inv = isvc.inventory.store
    dev = inv.insert({"mac": "aa:bb:cc:00:00:70", "first_seen_ms": 0, "last_seen_ms": 0,
                      "name": "Smart plug", "last_ip": "192.168.2.70"})  # fmt: skip
    inv.upsert_ip(dev, "192.168.2.70", T_MS - 60_000, "arp")
    ads = inv.insert({"mac": "aa:bb:cc:00:00:71", "first_seen_ms": 0, "last_seen_ms": 0,
                      "last_ip": "192.168.2.71"})  # fmt: skip
    inv.upsert_ip(ads, "192.168.2.71", T_MS - 60_000, "arp")
    seed_history(isvc, dev, "192.168.2.70", q=100, b=5)
    seed_history(isvc, ads, "192.168.2.71", q=100, b=60)  # always mostly blocked: its normal
    burst(isvc.fake, 120, "192.168.2.70", blocked=90)
    burst(isvc.fake, 120, "192.168.2.71", start=-45.0, blocked=90)
    assert await isvc.poll(T_MS)
    [e] = anomalies(isvc, "dns_blocked_rate")
    assert e["title"] == (
        "75% of Smart plug's DNS queries were blocked in the last hour (usually 5%)"
    )
    assert anomalies(isvc, "dns_query_rate") == []


def test_device_dns_endpoint_and_page_lists(client):
    import time as _time

    from netwatch.services.adguard.clients import hour_of

    st = client.app.state
    dev = st.device_store.insert({"mac": "aa:bb:cc:00:00:80", "first_seen_ms": 0,
                                  "last_seen_ms": 0, "name": "Office PC"})  # fmt: skip
    h = hour_of(int(_time.time() * 1000))
    st.adguard_clients.put([
        {"hour_ms": h, "client": "192.168.2.80", "device_id": dev, "queries": 50, "blocked": 10,
         "failed": 1, "domains": {"github.com": 30}, "blocked_domains": {"ads.example": 10}},
        {"hour_ms": h, "client": "192.168.2.81", "device_id": None, "queries": 9, "blocked": 0,
         "failed": 0, "domains": {"github.com": 9}, "blocked_domains": {}},
    ])  # fmt: skip
    r = client.get(f"/api/devices/{dev}/dns").json()
    assert r["configured"] is False and r["hours"] == 24
    assert r["queries"] == 50 and r["blocked_pct"] == 20.0
    assert r["top_domains"] == [{"domain": "github.com", "queries": 30}]
    assert r["top_blocked"] == [{"domain": "ads.example", "queries": 10}]
    assert client.get(f"/api/devices/{dev}/dns?hours=169").status_code == 422
    assert client.get("/api/devices/99999/dns").status_code == 404
    page = client.get("/api/adguard").json()
    assert page["top_domains"][0] == {"domain": "github.com", "queries": 39}
    assert page["unknown_clients"] == [{"client": "192.168.2.81", "queries": 9}]
    assert page["dhcp"] is None


def test_dns_endpoints_require_sign_in(anon_client):
    for path in ("/api/adguard", "/api/devices/1/dns", "/api/admin/adguard"):
        assert anon_client.get(path).status_code == 401


# ── actions (Phase 3) ────────────────────────────────────────────────────────


@pytest.fixture
def acts(svc):
    from types import SimpleNamespace

    from netwatch.services.adguard.actions import AdGuardActions

    settings = SimpleNamespace(adguard_actions=True)
    a = AdGuardActions(svc, settings, gateway_ip=lambda: "192.168.2.1")
    svc.actions = a
    a.settings_ns = settings
    return a


async def test_actions_refuse_when_turned_off(acts):
    from netwatch.services.adguard.actions import ActionsDisabled

    acts.settings_ns.adguard_actions = False
    calls = [
        lambda: acts.pause(30, "admin"),
        lambda: acts.resume("admin"),
        lambda: acts.add_rule("x.com", "block", "a"),
        lambda: acts.switch_upstreams(["tls://9.9.9.9"]),
        lambda: acts.test_upstreams(["9.9.9.9"]),
    ]
    for call in calls:
        with pytest.raises(ActionsDisabled):
            await call()
    assert acts.service.fake.posts == []


async def test_pause_resumes_after_its_time_even_after_a_restart(acts, svc):
    from netwatch.services.adguard.actions import AdGuardActions

    f = svc.fake
    f.honour_duration = False  # an older AdGuard Home that ignores the duration
    p = await acts.pause(30, "admin")
    assert f.posts[-1] == ("/control/protection", {"enabled": False, "duration": 1_800_000})
    assert f.protection is False and acts.pause_state()["minutes"] == 30
    # The poll sees protection off: an information event, not a warning.
    assert await svc.poll()
    [e] = dns_events(svc, "protection_disabled")
    assert e["severity"] == "info" and e["details"]["by"] == "admin"
    # A NetWatch restart: a new actions object reads the pause from the database.
    again = AdGuardActions(svc, acts.settings_ns)
    assert not await again.tick(p["until_ms"] - 1000)
    acts.settings_ns.adguard_actions = False  # turning actions off never leaves it paused
    assert await again.tick(p["until_ms"] + 1000)
    assert f.protection is True and again.pause_state() is None
    assert any(ev["title"].startswith("AdGuard Home protection turned back on")
               for ev in svc.events.list(0, 10**13, kinds=["security"]))  # fmt: skip
    assert not await again.tick(p["until_ms"] + 2000)  # nothing left to do


async def test_pause_limits_and_resume(acts, svc):
    from netwatch.services.adguard.actions import ActionError

    for bad in (0, 1441):
        with pytest.raises(ActionError):
            await acts.pause(bad, "admin")
    await acts.pause(5, "admin")
    await acts.resume("admin")
    assert svc.fake.protection is True and acts.pause_state() is None


async def test_first_rule_on_an_adguard_with_no_custom_rules(acts, svc):
    svc.fake.user_rules = None  # AdGuard Home answers "user_rules": null until a rule exists
    entry = await acts.add_rule("buynowthinklater.us.to", "allow", "admin")
    assert entry["rule"] == "@@||buynowthinklater.us.to^"
    assert svc.fake.user_rules == ["@@||buynowthinklater.us.to^"]


async def test_rules_touch_only_netwatch_lines(acts, svc):
    from netwatch.services.adguard.actions import ActionError

    f = svc.fake
    entry = await acts.add_rule("Ads.Example.COM.", "allow", "admin")
    assert entry["rule"] == "@@||ads.example.com^"
    assert f.user_rules == ["||doubleclick.net^", "# my own rule", "@@||ads.example.com^"]
    # Blocking what NetWatch allowed replaces NetWatch's own line.
    await acts.add_rule("ads.example.com", "block", "admin")
    assert f.user_rules == ["||doubleclick.net^", "# my own rule", "||ads.example.com^"]
    assert [r["rule"] for r in acts.rules()] == ["||ads.example.com^"]
    # Your own rules stay yours.
    with pytest.raises(ActionError, match="your own rules"):
        await acts.add_rule("doubleclick.net", "block", "admin")
    with pytest.raises(ActionError, match="only removes rules it added"):
        await acts.remove_rule("||doubleclick.net^")
    for bad in ("*.example.com", "https://x.com/a", "192.168.2.53", "localhost", "a b.com", ""):
        with pytest.raises(ActionError):
            await acts.add_rule(bad, "block", "admin")
    await acts.remove_rule("||ads.example.com^")
    assert f.user_rules == ["||doubleclick.net^", "# my own rule"] and acts.rules() == []
    assert await acts.add_rule("bücher.example", "block", "admin")
    assert f.user_rules[-1] == "||xn--bcher-kva.example^"


async def test_upstream_switch_is_tested_and_can_be_undone(acts, svc):
    from netwatch.services.adguard.actions import ActionError

    f = svc.fake
    f.bad_upstreams = {"udp://10.9.9.9"}
    t = await acts.test_upstreams(["tls://9.9.9.9", "udp://10.9.9.9"])
    assert not t["ok"] and t["results"]["tls://9.9.9.9"] == "OK"
    with pytest.raises(ActionError, match=r"could not use udp://10\.9\.9\.9"):
        await acts.switch_upstreams(["udp://10.9.9.9"])
    assert f.upstream_dns == ["tls://1.1.1.1", "tls://1.0.0.1"]  # unchanged
    for bad in ([], ["ftp://x"], ["a b"], ["1.1.1.1:99999"], [f"9.9.9.{i}" for i in range(6)]):
        with pytest.raises(ActionError):
            await acts.test_upstreams(bad)
    r = await acts.switch_upstreams(["tls://dns.quad9.net", "9.9.9.9:53", "[2620:fe::fe]:53"])
    assert r["previous"] == ["tls://1.1.1.1", "tls://1.0.0.1"]
    assert f.upstream_dns == ["tls://dns.quad9.net", "9.9.9.9:53", "[2620:fe::fe]:53"]
    assert acts.state()["previous_upstreams"]["upstreams"] == ["tls://1.1.1.1", "tls://1.0.0.1"]
    await acts.restore_upstreams()
    assert f.upstream_dns == ["tls://1.1.1.1", "tls://1.0.0.1"]
    assert len(acts.state()["presets"]) == 6


def test_actions_api_off_by_default_then_audited(client):
    from netwatch.services.events import SECURITY

    st = client.app.state
    fake = FakeAdGuard()
    st.adguard.client_factory = lambda cfg, pw: client_for(fake, pw)
    st.adguard.save(HOST, PORT, False, USER, True, PASSWORD)
    base = "/api/adguard/actions"
    s = client.get(base).json()
    assert s["enabled"] is False and s["upstreams"] is None and len(s["presets"]) == 6
    calls = [
        ("/pause", {"minutes": 30}),
        ("/resume", None),
        ("/rules", {"domain": "x.com", "kind": "block"}),
        ("/upstreams", {"upstreams": ["9.9.9.9"]}),
        ("/upstreams/test", {"upstreams": ["9.9.9.9"]}),
    ]
    for path, body in calls:
        r = client.post(base + path, json=body)
        assert r.status_code == 403 and "turned off" in r.json()["detail"]
    assert fake.posts == []

    assert (
        client.put("/api/admin/settings", json={"changes": {"adguard_actions": True}}).status_code
        == 200
    )
    s = client.get(base).json()
    assert s["enabled"] and s["upstreams"] == ["tls://1.1.1.1", "tls://1.0.0.1"]
    assert client.post(base + "/pause", json={"minutes": 0}).status_code == 422
    assert client.post(base + "/pause", json={"minutes": 30, "x": 1}).status_code == 422
    r = client.post(base + "/pause", json={"minutes": 30}).json()
    assert r["pause"]["minutes"] == 30 and fake.protection is False
    assert client.post(base + "/resume").status_code == 200 and fake.protection is True
    r = client.post(base + "/rules", json={"domain": "ads.example.com", "kind": "allow"}).json()
    assert r["rule"]["rule"] == "@@||ads.example.com^" and len(r["rules"]) == 1
    r = client.post(base + "/rules", json={"domain": "doubleclick.net", "kind": "block"})
    assert r.status_code == 422 and "your own rules" in r.json()["detail"]
    r = client.request("DELETE", base + "/rules", json={"rule": "@@||ads.example.com^"})
    assert r.status_code == 200 and r.json()["rules"] == []
    fake.bad_upstreams = {"udp://10.9.9.9"}
    t = client.post(base + "/upstreams/test", json={"upstreams": ["udp://10.9.9.9"]}).json()
    assert t["ok"] is False
    assert (
        client.post(base + "/upstreams", json={"upstreams": ["udp://10.9.9.9"]}).status_code == 422
    )
    r = client.post(base + "/upstreams", json={"upstreams": ["tls://dns.quad9.net"]}).json()
    assert r["previous"] == ["tls://1.1.1.1", "tls://1.0.0.1"]
    assert client.post(base + "/upstreams/restore").json()["current"] == [
        "tls://1.1.1.1",
        "tls://1.0.0.1",
    ]

    titles = [e["title"] for e in st.events.list(0, 10**13, kinds=[SECURITY])]
    joined = "\n".join(titles)
    for want in ("protection paused for 30 min (until", "protection turned back on",
                 "allowed ads.example.com", "removed rule @@||ads.example.com^",
                 "could not block doubleclick.net", "could not switch the upstream DNS",
                 "upstream DNS switched from tls://1.1.1.1, tls://1.0.0.1 to "
                 "tls://dns.quad9.net"):  # fmt: skip
        assert want in joined, want
    assert PASSWORD not in joined and PASSWORD not in json.dumps(client.get(base).json())


def test_actions_need_sign_in_and_the_csrf_header(anon_client, client):
    assert anon_client.get("/api/adguard/actions").status_code == 401
    assert anon_client.post("/api/adguard/actions/resume").status_code in (401, 403)
    r = client.post("/api/adguard/actions/resume", headers={"X-Requested-With": ""})
    assert r.status_code == 403


# ── DHCP reservations ────────────────────────────────────────────────────────

DHCP_V4 = {"gateway_ip": "192.168.2.1", "subnet_mask": "255.255.255.0",
           "range_start": "192.168.2.100", "range_end": "192.168.2.200",
           "lease_duration": 86400}  # fmt: skip


def dhcp_on(fake):
    fake.dhcp = {
        "enabled": True, "interface_name": "eth0", "v4": dict(DHCP_V4),
        "leases": [{"mac": "aa:bb:cc:00:00:50", "ip": "192.168.2.150", "hostname": "pixel-9",
                    "expires": "2026-11-16T12:00:00Z"},
                   {"mac": "aa:bb:cc:00:00:60", "ip": "192.168.2.160", "hostname": "tv",
                    "expires": "2026-11-16T12:00:00Z"}],
        "static_leases": [{"mac": "aa:bb:cc:00:00:70", "ip": "192.168.2.20", "hostname": "nas"}],
    }  # fmt: skip


async def test_dhcp_status_reads_the_dhcp_settings():
    fake = FakeAdGuard()
    dhcp_on(fake)
    d = await client_for(fake).dhcp_status()
    assert d["v4"] == {k: DHCP_V4[k] for k in ("gateway_ip", "subnet_mask", "range_start",
                                                 "range_end")}  # fmt: skip
    fake.dhcp["v4"] = {"gateway_ip": "", "subnet_mask": "255.255.255.0"}
    assert (await client_for(fake).dhcp_status())["v4"] is None


async def test_reserve_checks_the_address(acts, svc):
    from netwatch.services.adguard.actions import ActionError

    f = svc.fake
    with pytest.raises(ActionError, match="not the DHCP server"):
        await acts.reserve("aa:bb:cc:00:00:50", "192.168.2.50", "pixel", "admin")
    dhcp_on(f)
    bad = {
        "10.0.0.5": "outside AdGuard Home's DHCP network \\(192.168.2.0/24\\)",
        "192.168.2.0": "network's own address",
        "192.168.2.255": "network's own address",
        "192.168.2.1": "gateway or AdGuard Home",
        HOST: "gateway or AdGuard Home",
        "192.168.2.20": "already reserved for nas",
        "192.168.2.160": "already in use by tv",
        "192.168.2": "IPv4 address",
        "fe80::1": "IPv4 address",
    }
    for ip, msg in bad.items():
        with pytest.raises(ActionError, match=msg):
            await acts.reserve("aa:bb:cc:00:00:50", ip, "pixel", "admin")
    with pytest.raises(ActionError, match="valid MAC"):
        await acts.reserve("not-a-mac", "192.168.2.50", "x", "admin")
    assert all(p[0] != "/control/dhcp/add_static_lease" for p in f.posts)

    # Its own current address is fine; the name is cleaned for AdGuard Home.
    r = await acts.reserve("AA-BB-CC-00-00-50", "192.168.2.150", "Sam's Pixel 9!", "admin")
    assert r == {"mac": "aa:bb:cc:00:00:50", "ip": "192.168.2.150", "hostname": "sam-s-pixel-9",
                 "changed_from": None, "private_mac": True, "by": "admin"}  # fmt: skip
    assert {"mac": "aa:bb:cc:00:00:50", "ip": "192.168.2.150", "hostname": "sam-s-pixel-9"} in (
        f.dhcp["static_leases"]
    )
    assert acts.reserved_by_netwatch() == ["aa:bb:cc:00:00:50"]
    with pytest.raises(ActionError, match=r"already reserved at 192\.168\.2\.150"):
        await acts.reserve("aa:bb:cc:00:00:50", "192.168.2.150", "sam-s-pixel-9", "admin")


async def test_change_puts_the_old_reservation_back_when_adguard_refuses(acts, svc):
    from netwatch.services.adguard.actions import ActionError

    f = svc.fake
    dhcp_on(f)
    r = await acts.reserve("aa:bb:cc:00:00:70", "192.168.2.21", "nas", "admin")
    assert r["changed_from"] == "192.168.2.20"
    assert {"mac": "aa:bb:cc:00:00:70", "ip": "192.168.2.21", "hostname": "nas"} in (
        f.dhcp["static_leases"]
    )
    f.refuse_lease = 1  # only the new address is refused; the old one goes back
    with pytest.raises(ActionError, match="refused the change"):
        await acts.reserve("aa:bb:cc:00:00:70", "192.168.2.22", "nas", "admin")
    assert [x for x in f.dhcp["static_leases"] if x["mac"] == "aa:bb:cc:00:00:70"] == [
        {"mac": "aa:bb:cc:00:00:70", "ip": "192.168.2.21", "hostname": "nas"}
    ]


async def test_unreserve_and_private_addresses(acts, svc):
    from netwatch.services.adguard.actions import ActionError

    f = svc.fake
    dhcp_on(f)
    from netwatch.services.adguard.actions import private_mac

    assert private_mac("ba:bb:cc:00:00:99") and not private_mac("00:11:22:33:44:55")
    r = await acts.reserve("ba:bb:cc:00:00:99", "192.168.2.99", "", "admin")
    assert r["private_mac"] and r["hostname"] is None
    await acts.unreserve("ba:bb:cc:00:00:99", "admin")
    assert all(x["mac"] != "ba:bb:cc:00:00:99" for x in f.dhcp["static_leases"])
    assert acts.reserved_by_netwatch() == []
    await acts.unreserve("aa:bb:cc:00:00:70", "admin")  # one made in AdGuard Home
    assert f.dhcp["static_leases"] == []
    with pytest.raises(ActionError, match="no reservation"):
        await acts.unreserve("aa:bb:cc:00:00:70", "admin")


async def test_reservations_refuse_when_actions_are_off(acts, svc):
    from netwatch.services.adguard.actions import ActionsDisabled

    dhcp_on(svc.fake)
    acts.settings_ns.adguard_actions = False
    for coro in (acts.dhcp(), acts.reserve("aa:bb:cc:00:00:50", "192.168.2.50", "x", "admin"),
                 acts.unreserve("aa:bb:cc:00:00:70", "admin")):  # fmt: skip
        with pytest.raises(ActionsDisabled):
            await coro
    assert svc.fake.posts == []


def test_reservations_api_and_security_log(client):
    from netwatch.services.events import SECURITY

    st = client.app.state
    fake = FakeAdGuard()
    dhcp_on(fake)
    st.adguard.client_factory = lambda cfg, pw: client_for(fake, pw)
    st.adguard.save(HOST, PORT, False, USER, True, PASSWORD)
    base = "/api/adguard/actions/dhcp"
    assert client.get(base).status_code == 403
    body = {"mac": "aa:bb:cc:00:00:50", "ip": "192.168.2.50"}
    assert client.post(base + "/reservations", json=body).status_code == 403
    client.put("/api/admin/settings", json={"changes": {"adguard_actions": True}})
    pixel = st.device_store.insert({"mac": "aa:bb:cc:00:00:50", "first_seen_ms": 1,
                                    "last_seen_ms": int(time.time() * 1000),
                                    "last_ip": "192.168.2.150", "name": "Sam's Pixel"})  # fmt: skip
    st.discovery.inventory.forget()
    d = client.get(base).json()
    assert d["enabled"] and d["v4"]["gateway_ip"] == "192.168.2.1"
    assert (
        d["reservations"][0]["mac"] == "aa:bb:cc:00:00:70"
        and not d["reservations"][0]["by_netwatch"]
    )
    lease = next(x for x in d["leases"] if x["mac"] == "aa:bb:cc:00:00:50")
    assert lease["device"] == {"id": pixel, "label": "Sam's Pixel"}

    r = client.post(base + "/reservations", json={"mac": "aa:bb:cc:00:00:50", "ip": "192.168.2.20"})
    assert r.status_code == 422 and "already reserved for nas" in r.json()["detail"]
    assert client.post(base + "/reservations", json={"mac": "x", "ip": "1"}).status_code == 422
    r = client.post(base + "/reservations/bulk", json={"device_ids": [pixel, 999999]}).json()
    assert r["results"][0] == {
        "id": pixel,
        "label": "Sam's Pixel",
        "ok": True,
        "ip": "192.168.2.150",
    }
    assert r["results"][1] == {"id": 999999, "ok": False, "error": "unknown device"}
    d = client.get(base).json()
    mine = next(x for x in d["reservations"] if x["mac"] == "aa:bb:cc:00:00:50")
    assert mine["by_netwatch"] and mine["device"]["id"] == pixel
    again = client.post(base + "/reservations/bulk", json={"device_ids": [pixel]}).json()
    assert again["results"][0]["already"] is True
    r = client.post(
        base + "/reservations",
        json={"mac": "aa:bb:cc:00:00:50", "ip": "192.168.2.51", "hostname": "pixel"},
    )
    assert r.json()["reservation"]["changed_from"] == "192.168.2.150"
    r = client.request("DELETE", base + "/reservations", json={"mac": "aa:bb:cc:00:00:50"})
    assert r.status_code == 200 and r.json()["removed"]["ip"] == "192.168.2.51"

    joined = "\n".join(e["title"] for e in st.events.list(0, 10**13, kinds=[SECURITY]))
    for want in ("could not reserve 192.168.2.20: 192.168.2.20 is already reserved for nas",
                 "reserved 192.168.2.150 for Sam's Pixel (aa:bb:cc:00:00:50)",
                 "changed 192.168.2.150 to 192.168.2.51 for Sam's Pixel (aa:bb:cc:00:00:50)",
                 "removed the reservation of 192.168.2.51 for Sam's Pixel"):  # fmt: skip
        assert want in joined, want
    assert PASSWORD not in joined and PASSWORD not in json.dumps(client.get(base).json())


# ── settings per device and per group (persistent clients) ──────────────────


@pytest.fixture
def prof(acts, svc, store):
    from netwatch.services.adguard.profiles import ProfileSync
    from netwatch.services.device_groups import GroupStore
    from netwatch.services.discovery.inventory import DeviceStore, Inventory

    devs = DeviceStore(store.engine)
    inv = Inventory(devs)
    gs = GroupStore(store.engine)
    inv.groups = gs
    p = ProfileSync(acts, inv, gs)
    p.devs, p.gs, p.fake = devs, gs, svc.fake
    svc.fake.clients = {"clients": [{"name": "Office PC", "ids": ["192.168.2.20"]},
                                    {"name": "My TV", "ids": ["00:11:22:00:00:99"]}],
                        "auto_clients": []}  # fmt: skip
    return p


def _dev(p, mac, name, **kw):
    now = int(time.time() * 1000)
    return p.devs.insert({"mac": mac, "first_seen_ms": 0, "last_seen_ms": now, "name": name, **kw})


def _clients(p):
    return {c["name"]: c for c in p.fake.clients["clients"]}


async def test_profiles_build_netwatch_clients_and_device_beats_group(prof):
    kids = prof.gs.create("Kids", "rose", "child")["id"]
    ipad = _dev(prof, "00:11:22:00:00:01", "Sam's iPad")
    phone = _dev(prof, "00:11:22:00:00:02", "Sam's phone")
    _dev(prof, "da:11:22:00:00:12", None, merged_into=phone)  # its private address
    laptop = _dev(prof, "00:11:22:00:00:03", "School laptop")
    prof.gs.assign([ipad, phone, laptop], kids)
    r = await prof.set_group(kids, "strict")
    assert r["changes"] == [{"action": "added", "client": "NetWatch: Kids"}]
    c = _clients(prof)["NetWatch: Kids"]
    assert sorted(c["ids"]) == ["00:11:22:00:00:01", "00:11:22:00:00:02", "00:11:22:00:00:03",
                                "da:11:22:00:00:12"]  # fmt: skip
    assert c["filtering_enabled"] and c["parental_enabled"] and c["safebrowsing_enabled"]
    assert c["safesearch"]["youtube"] and not c["use_global_settings"]
    # The laptop gets its own profile: it leaves the Kids client.
    r = await prof.set_device(laptop, "no_blocking")
    assert {"action": "added", "client": "NetWatch: School laptop"} in r["changes"]
    cs = _clients(prof)
    assert "00:11:22:00:00:03" not in cs["NetWatch: Kids"]["ids"]
    own = cs["NetWatch: School laptop"]
    assert own["ids"] == ["00:11:22:00:00:03"] and not own["filtering_enabled"]
    assert own["use_global_blocked_services"] is False and own["blocked_services"] == []
    # Nothing to do the second time.
    assert (await prof.reconcile())["changes"] == []
    # Your own clients are untouched.
    assert cs["Office PC"] == {"name": "Office PC", "ids": ["192.168.2.20"]}


async def test_group_changes_follow_and_default_removes(prof):
    kids = prof.gs.create("Kids", "rose", "child")["id"]
    teens = prof.gs.create("Teens", "sky", "star")["id"]
    a = _dev(prof, "00:11:22:00:00:01", "Tablet")
    prof.gs.assign([a], kids)
    await prof.set_group(kids, "strict")
    await prof.set_group(teens, "no_blocking")
    assert "NetWatch: Teens" not in _clients(prof)  # no members yet: no client
    b = _dev(prof, "00:11:22:00:00:02", "Phone")
    prof.gs.assign([b], kids)
    await prof.reconcile()
    prof.gs.assign([b], None)  # leaving the group shrinks the client, and says so
    assert (await prof.reconcile())["changes"] == [
        {"action": "updated", "client": "NetWatch: Kids"}
    ]
    prof.gs.assign([a], teens)  # moving a device frees its MAC before it is added elsewhere
    r = await prof.reconcile()
    cs = _clients(prof)
    assert "NetWatch: Kids" not in cs and cs["NetWatch: Teens"]["ids"] == ["00:11:22:00:00:01"]
    assert {"action": "added", "client": "NetWatch: Teens"} in r["changes"]
    prof.gs.update(teens, name="Big kids")
    await prof.reconcile()
    assert "NetWatch: Big kids" in _clients(prof) and "NetWatch: Teens" not in _clients(prof)
    await prof.set_group(teens, "default")
    assert not any(n.startswith("NetWatch:") for n in _clients(prof)) and prof.tracked() == {}
    await prof.set_group(teens, "strict")
    prof.gs.delete(teens)
    await prof.reconcile()
    assert not any(n.startswith("NetWatch:") for n in _clients(prof))
    assert prof.profiles()["groups"] == {
        str(kids): {"filtering": "strict", "youtube": "default", "services": []}
    }  # the deleted group is dropped


async def test_a_mac_in_your_own_client_is_a_conflict(prof):
    from netwatch.services.adguard.actions import ActionError

    kids = prof.gs.create("Kids", "rose", "child")["id"]
    tv = _dev(prof, "00:11:22:00:00:99", "Living room TV")
    ipad = _dev(prof, "00:11:22:00:00:01", "iPad")
    prof.gs.assign([tv, ipad], kids)
    r = await prof.set_group(kids, "strict")
    assert r["conflicts"] == [{"device_id": tv, "label": "Living room TV",
                               "mac": "00:11:22:00:00:99", "client": "My TV",
                               "profile": {"filtering": "strict", "youtube": "default",
                                           "services": []}}]  # fmt: skip
    assert _clients(prof)["NetWatch: Kids"]["ids"] == ["00:11:22:00:00:01"]
    assert _clients(prof)["My TV"]["ids"] == ["00:11:22:00:00:99"]
    with pytest.raises(ActionError, match="profile must be"):
        await prof.set_group(kids, "loose")


async def test_profiles_refuse_when_actions_are_off(prof):
    from netwatch.services.adguard.actions import ActionsDisabled

    kids = prof.gs.create("Kids", "rose", "child")["id"]
    prof.actions.settings_ns.adguard_actions = False
    with pytest.raises(ActionsDisabled):
        await prof.set_group(kids, "strict")
    assert prof.fake.posts == []


def test_profiles_api_and_security_log(client):
    from netwatch.services.events import SECURITY

    st = client.app.state
    fake = FakeAdGuard()
    fake.clients = {
        "clients": [{"name": "My TV", "ids": ["00:11:22:00:00:99"]}],
        "auto_clients": [],
    }
    st.adguard.client_factory = lambda cfg, pw: client_for(fake, pw)
    st.adguard.save(HOST, PORT, False, USER, True, PASSWORD)
    base = "/api/adguard/actions/profiles"
    assert client.get(base).status_code == 403
    client.put("/api/admin/settings", json={"changes": {"adguard_actions": True}})
    now = int(time.time() * 1000)
    ipad = st.device_store.insert({"mac": "00:11:22:00:00:01", "first_seen_ms": 0,
                                   "last_seen_ms": now, "name": "Sam's iPad"})  # fmt: skip
    tv = st.device_store.insert({"mac": "00:11:22:00:00:99", "first_seen_ms": 0,
                                 "last_seen_ms": now, "name": "Living room TV"})  # fmt: skip
    kids = client.post("/api/device-groups", json={"name": "Kids", "colour": "rose",
                                                   "icon": "child"}).json()["id"]  # fmt: skip
    st.adguard_profiles.dirty = False
    client.post("/api/devices/bulk", json={"ids": [ipad, tv], "group_id": kids})
    assert st.adguard_profiles.dirty  # a group change asks for a sync
    st.discovery.inventory.forget()

    assert client.put(f"{base}/group/{kids}", json={"filtering": "loose"}).status_code == 422
    assert client.put(f"{base}/group/99999", json={"filtering": "strict"}).status_code == 404
    r = client.put(f"{base}/group/{kids}", json={"filtering": "strict"}).json()
    assert r["changes"] == [{"action": "added", "client": "NetWatch: Kids"}]
    assert r["conflicts"][0]["client"] == "My TV"
    g = client.get(base).json()
    assert g["profiles"]["groups"] == {
        str(kids): {"filtering": "strict", "youtube": "default", "services": []}
    }
    kids_client = next(c for c in g["managed"] if c["key"] == f"group:{kids}")
    assert kids_client["devices"] == [{"id": ipad, "label": "Sam's iPad"}]
    r = client.put(f"{base}/device/{ipad}", json={"filtering": "no_blocking"}).json()
    assert {"action": "added", "client": "NetWatch: Sam's iPad"} in r["changes"]
    assert {"action": "removed", "client": "NetWatch: Kids"} in r["changes"]
    assert client.put(f"{base}/device/99999", json={"filtering": "strict"}).status_code == 404
    assert [x["id"] for x in client.get(base).json()["services_all"]][:2] == ["youtube", "tiktok"]
    r = client.put(
        f"{base}/group/{kids}",
        json={"filtering": "strict", "youtube": "blocked", "services": ["roblox"]},
    )
    assert r.status_code == 200
    bad = client.put(f"{base}/group/{kids}", json={"services": ["myspace"]})
    assert bad.status_code == 422 and "does not know the app" in bad.json()["detail"]
    assert client.put(f"{base}/group/{kids}", json={"youtube": "never"}).status_code == 422

    joined = "\n".join(e["title"] for e in st.events.list(0, 10**13, kinds=[SECURITY]))
    assert "AdGuard Home: the group Kids set to Strict (added NetWatch: Kids)" in joined
    assert "AdGuard Home: Sam's iPad set to No blocking (" in joined
    assert "the group Kids set to Strict, YouTube blocked, also blocks Roblox" in joined
    assert "could not set the group Kids to Default filtering, also blocks myspace" in joined
    assert PASSWORD not in joined and PASSWORD not in json.dumps(client.get(base).json())


async def test_youtube_and_app_settings_build_the_right_client(prof):
    from netwatch.services.adguard.actions import ActionError
    from netwatch.services.adguard.profiles import describe

    f = prof.fake
    f.blocked_global = ["tiktok"]  # blocked for everyone
    f.safebrowsing = True  # global settings a "default" client copies
    kids = prof.gs.create("Kids", "rose", "child")["id"]
    a = _dev(prof, "00:11:22:00:00:01", "Tablet")
    prof.gs.assign([a], kids)
    # YouTube Restricted Mode only: everything else follows the global settings.
    await prof.set_group(kids, {"youtube": "restricted"})
    c = _clients(prof)["NetWatch: Kids"]
    assert c["filtering_enabled"] and c["safebrowsing_enabled"] and not c["parental_enabled"]
    assert (
        c["safesearch"]["enabled"] and c["safesearch"]["youtube"] and not c["safesearch"]["google"]
    )
    assert c["use_global_blocked_services"] and c["blocked_services"] == ["tiktok"]
    # Block YouTube and Roblox: added to what everyone has blocked.
    await prof.set_group(
        kids, {"filtering": "strict", "youtube": "blocked", "services": ["roblox"]}
    )
    c = _clients(prof)["NetWatch: Kids"]
    assert not c["use_global_blocked_services"]
    assert c["blocked_services"] == ["roblox", "tiktok", "youtube"]
    assert prof.profiles()["groups"][str(kids)] == {"filtering": "strict", "youtube": "blocked",
                                                    "services": ["roblox"]}  # fmt: skip
    assert describe(prof.profiles()["groups"][str(kids)], {"roblox": "Roblox"}) == (
        "Strict, YouTube blocked, also blocks Roblox"
    )
    # Allowed: YouTube comes off even when everyone else has it blocked.
    f.blocked_global = ["tiktok", "youtube"]
    await prof.set_group(kids, {"youtube": "allowed"})
    c = _clients(prof)["NetWatch: Kids"]
    assert c["blocked_services"] == ["tiktok"] and not c["use_global_blocked_services"]
    # A change to the global settings is picked up on the next check.
    f.parental = True
    assert (await prof.reconcile())["changes"] == [
        {"action": "updated", "client": "NetWatch: Kids"}
    ]
    assert _clients(prof)["NetWatch: Kids"]["parental_enabled"]
    # No blocking with one app blocked: only that app.
    await prof.set_group(kids, {"filtering": "no_blocking", "services": ["discord"]})
    c = _clients(prof)["NetWatch: Kids"]
    assert not c["filtering_enabled"] and c["blocked_services"] == ["discord"]
    with pytest.raises(ActionError, match="does not know the app"):
        await prof.set_group(kids, {"services": ["myspace"]})
    with pytest.raises(ActionError, match="YouTube must be"):
        await prof.set_group(kids, {"youtube": "sometimes"})


async def test_v017_profiles_are_read_as_filtering(prof):
    from netwatch.services.adguard.profiles import PROFILES_KEY

    kids = prof.gs.create("Kids", "rose", "child")["id"]
    prof.store.kv_set(
        PROFILES_KEY,
        {
            "devices": {},
            "groups": {str(kids): {"filtering": "strict", "youtube": "default", "services": []}},
        },
    )
    assert prof.profiles()["groups"][str(kids)] == {"filtering": "strict", "youtube": "default",
                                                    "services": []}  # fmt: skip


def test_integrations_state_and_turning_polling_on(client):
    from netwatch.services.events import SECURITY

    st = client.app.state
    fake = FakeAdGuard()
    st.adguard.client_factory = lambda cfg, pw: client_for(fake, pw)
    st.adguard.save(HOST, PORT, False, USER, False, PASSWORD)  # saved with polling unticked
    s = client.get("/api/admin/integrations").json()
    assert s["adguard"] == {"label": "AdGuard Home", "state": "not_polling", "paused": None}
    assert s["deco"]["state"] == "off" and s["hub"]["state"] == "off"
    assert (
        client.post("/api/admin/integrations/deco/polling", json={"enabled": True}).status_code
        == 422
    )
    assert (
        client.post("/api/admin/integrations/nas/polling", json={"enabled": True}).status_code
        == 422
    )
    r = client.post("/api/admin/integrations/adguard/polling", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] and PASSWORD not in r.text
    cfg = st.adguard.config()
    assert (
        cfg["enabled"] and cfg["host"] == HOST and cfg["username"] == USER
    )  # nothing else changed
    assert st.adguard.configured  # the password is kept
    assert client.get("/api/admin/integrations").json()["adguard"]["state"] in ("waiting", "ok")
    titles = [e["title"] for e in st.events.list(0, 10**13, kinds=[SECURITY])]
    assert "AdGuard Home polling turned on" in titles
