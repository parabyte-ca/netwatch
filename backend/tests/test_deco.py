from __future__ import annotations

import base64
import json
import math
import time
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from netwatch.services.deco.client import (
    DecoClient,
    DecoError,
    DecoLoginError,
    aes_decrypt,
    aes_encrypt,
)
from netwatch.services.deco.service import (
    DISCONNECT_AFTER_MS,
    PASSWORD_KEY,
    DecoService,
    normalize_client,
    normalize_deco,
)
from netwatch.services.diagnosis import Diagnoser
from netwatch.services.discovery.inventory import DeviceStore, Inventory
from netwatch.services.events import WIFI, EventLog
from netwatch.services.netinfo import DefaultRoute
from netwatch.services.pathtest import PathStore
from netwatch.services.secrets import SecretBox

from .conftest import make_target

PASSWORD = "owner-pass"
MAIN, SAT = "AA-BB-CC-00-00-01", "AA-BB-CC-00-00-02"
PHONE, TV = "11:22:33:44:55:66", "11:22:33:44:55:77"


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ── fake Deco (LuCI protocol) ─────────────────────────────────────────────────


class FakeDeco:
    """Speaks the Deco's encrypted LuCI API, enough for login, device and client lists."""

    def __init__(self, password: str = PASSWORD) -> None:
        self.password = password
        self.pw_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505 - test speed
        self.sign_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505 - test speed
        self.seq = 1000
        self.stok = "stok123"
        self.cookie = "sysauth=cafe42"
        self.aes: tuple[bytes, bytes] | None = None
        self.logins = 0
        self.expire_next = False
        self.decos = [
            {
                "mac": MAIN,
                "custom_nickname": b64("Living Room"),
                "device_ip": "192.168.2.10",
                "device_model": "X55",
                "role": "master",
                "group_status": "connected",
                "connection_type": ["wired"],
                "software_ver": "1.2",
            },
            {
                "mac": SAT,
                "nickname": b64("office"),
                "device_ip": "192.168.2.11",
                "device_model": "X55",
                "role": "slave",
                "group_status": "connected",
                "connection_type": ["band5"],
                "software_ver": "1.2",
            },
        ]
        self.clients = {
            MAIN: [
                {
                    "mac": PHONE,
                    "name": b64("Pixel"),
                    "ip": "192.168.2.50",
                    "online": True,
                    "connection_type": "band5",
                    "interface": "main",
                }
            ],
            SAT: [
                {
                    "mac": TV,
                    "name": b64("Living TV"),
                    "ip": "192.168.2.51",
                    "online": True,
                    "connection_type": "wired",
                    "interface": "main",
                }
            ],
        }

    @staticmethod
    def _pub(key) -> list[str]:
        n = key.public_key().public_numbers()
        return [format(n.n, "x"), format(n.e, "x")]

    @staticmethod
    def _rsa_decrypt(key, hexdata: str) -> bytes:
        size = (int(math.log2(key.public_key().public_numbers().n)) + 8) >> 3
        raw = bytes.fromhex(hexdata)
        return b"".join(
            key.decrypt(raw[i : i + size], padding.PKCS1v15()) for i in range(0, len(raw), size)
        )

    def _decode(self, body: str) -> dict:
        form = parse_qs(body)
        sign = self._rsa_decrypt(self.sign_key, form["sign"][0]).decode()
        parts = dict(p.split("=", 1) for p in sign.split("&"))
        self.aes = (parts["k"].encode(), parts["i"].encode())
        data = form["data"][0]
        assert int(parts["s"]) == self.seq + len(data)
        return json.loads(aes_decrypt(*self.aes, base64.b64decode(data)))

    def _reply(self, data: dict, **kw) -> httpx.Response:
        assert self.aes is not None
        enc = base64.b64encode(aes_encrypt(*self.aes, json.dumps(data).encode())).decode()
        return httpx.Response(200, json={"error_code": 0, "data": enc}, **kw)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, form = req.url.path, req.url.params.get("form")
        body = req.content.decode()
        if path == "/cgi-bin/luci/;stok=/login":
            if form == "keys":
                return httpx.Response(200, json={"result": {"password": self._pub(self.pw_key)}})
            if form == "auth":
                return httpx.Response(
                    200, json={"result": {"key": self._pub(self.sign_key), "seq": self.seq}}
                )
            payload = self._decode(body)
            pw = self._rsa_decrypt(self.pw_key, payload["params"]["password"]).decode()
            if pw != self.password:
                return self._reply({"error_code": -5002, "result": {"attemptsAllowed": 9}})
            self.logins += 1
            return self._reply(
                {"error_code": 0, "result": {"stok": self.stok}},
                headers={"set-cookie": f"{self.cookie}; path=/"},
            )
        assert path.startswith(f"/cgi-bin/luci/;stok={self.stok}/")
        if self.expire_next or req.headers.get("cookie") != self.cookie:
            self.expire_next = False
            return httpx.Response(403)
        payload = self._decode(body)
        if form == "device_list":
            return self._reply({"error_code": 0, "result": {"device_list": self.decos}})
        if form == "client_list":
            mac = payload["params"]["device_mac"]
            return self._reply(
                {"error_code": 0, "result": {"client_list": self.clients.get(mac, [])}}
            )
        return httpx.Response(404)


@pytest.fixture
def fake():
    return FakeDeco()


def client_for(fake, password=PASSWORD) -> DecoClient:
    return DecoClient("192.168.2.10", password, transport=httpx.MockTransport(fake))


async def test_client_signs_in_and_lists(fake):
    c = client_for(fake)
    decos = await c.list_decos()
    assert [d.get("custom_nickname") or d["nickname"] for d in decos] == ["Living Room", "office"]
    clients = await c.list_clients(MAIN)
    assert clients[0]["name"] == "Pixel" and fake.logins == 1
    await c.aclose()


async def test_client_signs_in_again_when_the_session_ends(fake):
    c = client_for(fake)
    await c.list_decos()
    fake.expire_next = True  # e.g. the owner opened the Deco app
    await c.list_decos()
    assert fake.logins == 2
    await c.aclose()


async def test_wrong_password_is_explained(fake):
    c = client_for(fake, "nope")
    with pytest.raises(DecoLoginError, match="wrong owner password"):
        await c.list_decos()
    await c.aclose()


async def test_unreachable_deco():
    def boom(req):
        raise httpx.ConnectError("refused")

    c = DecoClient("192.168.2.10", PASSWORD, transport=httpx.MockTransport(boom))
    with pytest.raises(DecoError, match="cannot reach"):
        await c.list_decos()
    await c.aclose()


def test_normalizers():
    d = normalize_deco(
        {
            "mac": SAT,
            "nickname": "office_upstairs",
            "role": "slave",
            "connection_type": ["band5", "band2_4"],
        }
    )
    assert d["mac"] == "aa:bb:cc:00:00:02" and d["raw_mac"] == SAT
    assert d["name"] == "Office Upstairs" and d["role"] == "satellite"
    assert d["backhaul"] == "5 GHz + 2.4 GHz" and d["online"]
    c = normalize_client({"mac": PHONE, "connection_type": "band2_4", "ip": "0.0.0.0"}, "x")
    assert c["connection"] == "2.4 GHz" and c["ip"] is None and not c["wired"]


# ── secrets ───────────────────────────────────────────────────────────────────


def test_secret_box_key_outside_db(tmp_path):
    box = SecretBox(tmp_path)
    token = box.encrypt("hunter2")
    assert "hunter2" not in token and box.decrypt(token) == "hunter2"
    key = tmp_path / ".secret_key"
    assert key.stat().st_mode & 0o777 == 0o600
    assert SecretBox(tmp_path).decrypt(token) == "hunter2"  # same key file
    key.unlink()
    assert SecretBox(tmp_path).decrypt(token) is None  # key lost → re-enter


# ── service: inventory and Wi-Fi events ──────────────────────────────────────


@pytest.fixture
def svc(store, tmp_path, fake):
    events = EventLog(store.engine)
    devices = DeviceStore(store.engine)
    inv = Inventory(devices)
    s = DecoService(
        store,
        events,
        devices,
        inv,
        SecretBox(tmp_path / "data"),
        client_factory=lambda host, pw: client_for(fake, pw),
    )
    s.save("192.168.2.10", True, PASSWORD)
    return s


def wifi(svc):
    return svc.events.list(0, int(time.time() * 1000) + 10**9, kinds=[WIFI])


async def test_poll_feeds_the_inventory(svc):
    assert svc.store.kv_get(PASSWORD_KEY) != PASSWORD  # encrypted at rest
    assert await svc.poll()
    phone = svc.devices.get_by_mac(PHONE.lower())
    assert phone["details"]["deco_name"] == "Living Room"
    assert phone["details"]["connection"] == "5 GHz" and phone["details"]["app_name"] == "Pixel"
    deco = svc.devices.get_by_mac("aa:bb:cc:00:00:02")
    assert deco["details"]["is_deco"] and deco["type"] == "access_point"
    st = svc.status()
    assert st["password_set"] and st["error"] is None and len(st["decos"]) == 2
    assert "password" not in json.dumps(st).replace("password_set", "")


def monitor(svc, store, mac):
    t = make_target(store, name="Phone", host="192.168.2.50", group="device")
    dev = svc.devices.get_by_mac(mac)
    svc.devices.update(dev["id"], {"target_id": t["id"]})
    return t


async def test_deco_offline_and_backhaul_events(svc, fake):
    now = 1_000_000
    await svc.poll(now)
    fake.decos[1]["connection_type"] = ["band2_4"]
    await svc.poll(now + 30_000)
    fake.decos[1]["group_status"] = "disconnected"
    await svc.poll(now + 60_000)
    fake.decos[1]["group_status"] = "connected"
    await svc.poll(now + 120_000)
    evs = {e["details"]["type"]: e for e in wifi(svc)}
    assert "5 GHz → 2.4 GHz" in evs["backhaul"]["title"]
    off = evs["deco_offline"]
    assert off["title"] == "Deco Office offline" and off["severity"] == "critical"
    assert (off["start_ms"], off["end_ms"]) == (now + 60_000, now + 120_000)


async def test_roam_and_disconnect_of_monitored_device(svc, store, fake):
    now = 2_000_000
    await svc.poll(now)
    monitor(svc, store, PHONE.lower())
    # unmonitored TV moving is not recorded
    fake.clients[MAIN].append(fake.clients[SAT].pop())
    fake.clients[SAT].append(fake.clients[MAIN].pop(0))  # phone roams to the Office
    await svc.poll(now + 30_000)
    roams = [e for e in wifi(svc) if e["details"]["type"] == "roam"]
    assert len(roams) == 1 and "Living Room (5 GHz) → Office (5 GHz)" in roams[0]["title"]
    # gone briefly: no event
    fake.clients[SAT] = []
    await svc.poll(now + 60_000)
    fake.clients[SAT] = [{"mac": PHONE, "online": True, "connection_type": "band5"}]
    await svc.poll(now + 90_000)
    assert not [e for e in wifi(svc) if e["details"]["type"] == "disconnect"]
    # gone for more than a minute: one event starting when it vanished
    fake.clients[SAT] = []
    for i in range(1, 5):
        await svc.poll(now + 90_000 + i * 30_000)
    disc = [e for e in wifi(svc) if e["details"]["type"] == "disconnect"]
    assert len(disc) == 1 and disc[0]["start_ms"] == now + 120_000 and disc[0]["end_ms"] is None
    assert DISCONNECT_AFTER_MS == 60_000
    fake.clients[MAIN] = [{"mac": PHONE, "online": True, "connection_type": "band2_4"}]
    await svc.poll(now + 300_000)
    disc = next(e for e in wifi(svc) if e["details"]["type"] == "disconnect")
    assert disc["end_ms"] == now + 300_000 and "Living Room" in disc["details"]["back_on"]


async def test_poll_error_is_reported(svc, fake):
    fake.password = "changed"
    svc._client = None
    svc._client_key = None
    assert not await svc.poll()
    assert "wrong owner password" in svc.status()["error"]


# ── diagnosis rules ───────────────────────────────────────────────────────────


async def test_diagnosis_blames_the_offline_deco(svc, store, fake):
    now = int(time.time() * 1000) // 1000 * 1000 - 600_000
    await svc.poll(now)
    t1 = make_target(store, name="TV", host="192.168.2.51", group="device")
    t2 = make_target(store, name="Laptop", host="192.168.2.52", group="device")
    for t in (t1, t2):
        svc.events.open(
            "outage",
            "critical",
            now + 60_000,
            f"{t['name']} unreachable",
            target_id=t["id"],
            end_ms=now + 180_000,
        )
    fake.decos[1]["group_status"] = "disconnected"
    await svc.poll(now + 50_000)
    diag = Diagnoser(
        store,
        svc.events,
        PathStore(store.engine),
        gateway=lambda: DefaultRoute("br0", "192.168.2.1"),
        devices=svc.devices,
    )
    inc = diag.incidents(now, now + 300_000)[0]
    assert inc.causes[0].id == "deco_offline" and "Office" in inc.causes[0].title
    assert inc.as_dict(now)["wifi_events"][0]["type"] == "deco_offline"


async def test_diagnosis_shared_deco_and_wifi_disconnect(svc, store, fake):
    now = int(time.time() * 1000) // 1000 * 1000 - 600_000
    fake.clients[SAT].append(
        {
            "mac": "11:22:33:44:55:88",
            "name": b64("Laptop"),
            "online": True,
            "connection_type": "band5",
        }
    )
    await svc.poll(now)
    tv = make_target(store, name="TV", host="192.168.2.51", group="device")
    lap = make_target(store, name="Laptop", host="192.168.2.52", group="device")
    svc.devices.update(svc.devices.get_by_mac(TV.lower())["id"], {"target_id": tv["id"]})
    svc.devices.update(svc.devices.get_by_mac("11:22:33:44:55:88")["id"], {"target_id": lap["id"]})
    for t in (tv, lap):
        svc.events.open(
            "outage",
            "critical",
            now + 60_000,
            f"{t['name']} unreachable",
            target_id=t["id"],
            end_ms=now + 180_000,
        )
    diag = Diagnoser(
        store,
        svc.events,
        PathStore(store.engine),
        gateway=lambda: DefaultRoute("br0", "192.168.2.1"),
        devices=svc.devices,
    )
    causes = diag.incidents(now, now + 300_000)[0].causes
    assert causes[0].id == "deco_shared" and "Office" in causes[0].title
    # a monitored device that dropped off Wi-Fi
    svc.events.open(
        WIFI,
        "warning",
        now + 50_000,
        "TV disconnected from the Decos",
        end_ms=now + 200_000,
        details={"mac": TV.lower(), "type": "disconnect"},
    )
    causes = diag.incidents(now, now + 300_000)[0].causes
    assert {"deco_shared", "wifi_disconnect"} <= {c.id for c in causes}


# ── API ───────────────────────────────────────────────────────────────────────


def test_deco_admin_api(client, fake):
    st = client.app.state
    st.deco.client_factory = lambda host, pw: client_for(fake, pw)
    r = client.put(
        "/api/admin/deco", json={"host": "8.8.8.8", "enabled": True, "password": PASSWORD}
    )
    assert r.status_code == 422 and "LAN" in r.text
    r = client.put("/api/admin/deco", json={"host": "http://x/", "enabled": True})
    assert r.status_code == 422
    r = client.post("/api/admin/deco/test", json={"host": "192.168.2.10", "password": PASSWORD})
    assert r.json()["ok"] and len(r.json()["decos"]) == 2 and r.json()["clients"] == 2
    r = client.post("/api/admin/deco/test", json={"host": "192.168.2.10", "password": "x"})
    assert not r.json()["ok"] and "wrong owner password" in r.json()["error"]
    r = client.put(
        "/api/admin/deco", json={"host": "192.168.2.10", "enabled": True, "password": PASSWORD}
    )
    body = r.json()
    assert r.status_code == 200 and body["password_set"] and body["enabled"]
    assert PASSWORD not in r.text
    assert st.store.kv_get(PASSWORD_KEY) and PASSWORD not in json.dumps(
        st.store.kv_get(PASSWORD_KEY)
    )
    # test with the stored password
    assert client.post("/api/admin/deco/test", json={"host": "192.168.2.10"}).json()["ok"]
    log = client.get("/api/admin/security-log").json()
    assert any("owner password saved" in e["title"] for e in log)
    r = client.put(
        "/api/admin/deco", json={"host": "192.168.2.10", "enabled": False, "forget_password": True}
    )
    assert not r.json()["password_set"]


def test_map_api_and_layout(client, fake):
    st = client.app.state
    make_target(st.store, name="Gateway", host="192.168.2.1", group="gateway")
    make_target(st.store, name="Cloudflare", host="1.1.1.1", group="public")
    g = client.get("/api/map").json()
    ids = {n["id"] for n in g["nodes"]}
    assert {"internet", "isp", "hub", "switch", "truenas"} <= ids and not g["deco_api"]
    assert {(e["source"], e["target"]) for e in g["edges"]} >= {("hub", "isp"), ("hub", "switch")}

    # with Deco data
    st.deco.client_factory = lambda host, pw: client_for(fake, pw)
    st.deco.save("192.168.2.10", True, PASSWORD)
    client.portal.call(st.deco.poll)
    tv = st.deco.devices.get_by_mac(TV.lower())
    st.deco.devices.update(tv["id"], {"name": "Living TV"})
    st.discovery.inventory.forget()
    g = client.get("/api/map").json()
    nodes = {n["id"]: n for n in g["nodes"]}
    main, sat = "deco:aa:bb:cc:00:00:01", "deco:aa:bb:cc:00:00:02"
    assert nodes[main]["info"]["role"] == "main" and nodes[sat]["label"] == "Deco Office"
    edges = {(e["source"], e["target"]): e for e in g["edges"]}
    assert edges[("hub", main)]["medium"] == "wired"
    assert edges[(main, sat)]["medium"] == "wireless"
    assert edges[(sat, f"dev:{tv['id']}")]["medium"] == "wired"
    assert nodes[f"group:{main}"]["info"]["count"] == 1  # the phone, unnamed

    # replay a past moment and an unknown incident
    assert client.get(f"/api/map?at={int(time.time() * 1000) - 60_000}").status_code == 200
    assert client.get("/api/map?incident=i123").status_code == 404
    assert client.get("/api/map?at=99999999999999").status_code == 422

    # layout
    assert client.get("/api/map/layout").json() == {"positions": {}}
    r = client.put("/api/map/layout", json={"positions": {"hub": {"x": 10.04, "y": -5}}})
    assert r.json() == {"positions": {"hub": {"x": 10.0, "y": -5.0}}}
    assert client.get("/api/map/layout").json()["positions"]["hub"]["x"] == 10.0
    bad = client.put("/api/map/layout", json={"positions": {"hub": {"x": 1e9, "y": 0}}})
    assert bad.status_code == 422
    assert client.delete("/api/map/layout").status_code == 204
    assert client.get("/api/map/layout").json() == {"positions": {}}


def test_map_highlights_an_incident(client):
    st = client.app.state
    hub = make_target(st.store, name="Gateway", host="192.168.2.1", group="gateway")
    cf = make_target(st.store, name="Cloudflare", host="1.1.1.1", group="public")
    goog = make_target(st.store, name="Google", host="8.8.8.8", group="public")
    start = int(time.time() * 1000) - 600_000
    eids = [
        st.events.open(
            "outage",
            "critical",
            start,
            f"{t['name']} unreachable",
            target_id=t["id"],
            end_ms=start + 120_000,
        )
        for t in (cf, goog)
    ]
    del hub
    r = client.get(f"/api/map?event={eids[0]}")
    assert r.status_code == 200, r.text
    hl = r.json()["highlight"]
    assert hl["incident_id"] == f"i{start}" and "internet" in hl["failing_nodes"]
    assert r.json()["at_ms"] == start + 30_000
    assert (
        client.get(f"/api/map?incident=i{start}").json()["highlight"]["incident_id"] == f"i{start}"
    )


def test_highlight_marks_the_named_deco_only():
    from netwatch.services.netmap import NetMap

    nodes = [
        {"id": "deco:a", "kind": "deco", "label": "Deco Living Room", "state": "up", "members": []},
        {"id": "deco:b", "kind": "deco", "label": "Deco Office", "state": "down", "members": []},
        {"id": "dev:1", "kind": "device", "label": "Living TV", "state": "down",
         "members": [{"target_id": 4}]},
    ]  # fmt: skip
    inc = {"id": "i1", "start_ms": 1, "failing": [{"target_id": 4}],
           "causes": [{"layer": "mesh", "title": "Deco Office went offline"}]}  # fmt: skip
    hl = NetMap(None).highlight(inc, {"nodes": nodes})
    assert hl["cause_nodes"] == ["deco:b"] and hl["failing_nodes"] == ["dev:1"]
    inc["causes"] = [{"layer": "mesh", "title": "Deco mesh (main Deco or its link to the Hub)"}]
    assert NetMap(None).highlight(inc, {"nodes": nodes})["cause_nodes"] == ["deco:b"]
    nodes[1]["state"] = "up"
    assert len(NetMap(None).highlight(inc, {"nodes": nodes})["cause_nodes"]) == 2
