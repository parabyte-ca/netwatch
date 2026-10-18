from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import parse_qs, unquote

import httpx
import pytest

from netwatch.services.events import HUB, OUTAGE
from netwatch.services.hub.client import HubClient, HubError, HubLoginError, optical_dbm, redact
from netwatch.services.hub.service import HubService, HubStore
from netwatch.services.secrets import SecretBox

PASSWORD = "hub-admin-pass"


class FakeHub:
    """Speaks the Sagemcom JSON-RPC and checks every request's auth-key."""

    def __init__(self, method: str = "sha512", gui: bool = True, password: str = PASSWORD):
        self.method, self.gui, self.password = method, gui, password
        self.session, self.nonce = 0, ""
        self.uptime, self.ppp_uptime = 864_000, 50_000
        self.optical_status, self.ppp_status = "Up", "Connected"
        self.rx_raw = -19234
        self.logins = 0
        self.expire_next = False
        self.mappings = [
            {
                "uid": 1,
                "Enable": True,
                "Protocol": "TCP",
                "ExternalPort": 32400,
                "InternalPort": 32400,
                "InternalClient": "192.168.2.15",
                "Description": "Plex",
                "Creator": "USER",
            },
            {
                "uid": 2,
                "Enable": True,
                "Protocol": "UDP",
                "ExternalPort": 3074,
                "InternalPort": 3074,
                "InternalClient": "192.168.2.40",
                "Description": "Xbox (3074) UPnP",
                "Creator": "UPNP",
            },
        ]
        self.dmz = {"Enable": False, "AdvancedDMZhost": ""}

    def h(self, s: str) -> str:
        f = hashlib.sha512 if self.method == "sha512" else hashlib.md5
        return f(s.encode()).hexdigest()

    def tree(self, xpath: str):
        values = {
            "Device/DeviceInfo": {
                "DeviceInfo": {
                    "ModelName": "Home Hub 4000",
                    "SoftwareVersion": "2.13.1",
                    "UpTime": self.uptime,
                }
            },
            "Device/Optical/Interfaces": [
                {
                    "uid": 1,
                    "Status": self.optical_status,
                    "OpticalSignalLevel": self.rx_raw,
                    "TransmitOpticalLevel": 2100,
                    "Stats": {"ErrorsReceived": "3"},
                }
            ],
            "Device/PPP/Interfaces": [
                {
                    "uid": 1,
                    "Enable": True,
                    "ConnectionStatus": self.ppp_status,
                    "LastChange": self.ppp_uptime,
                    "LastConnectionError": "ERROR_NONE",
                }
            ],
            "Device/IP/Interfaces": [
                {"uid": 1, "IPv4Addresses": [{"IPAddress": "192.168.2.1"}]},
                {"uid": 2, "IPv4Addresses": [{"IPAddress": "74.12.34.56"}]},
            ],
            "Device/WiFi/AccessPoints": [{"SSID": "home", "Security": {"KeyPassphrase": "s3cret"}}],
            "Device/NAT/PortMappings": self.mappings,
            "Device/UPnP/Device": {"Device": {"Enable": True, "UPnPIGD": True}},
            "Device/Services/BellNetworkCfg/AdvancedDMZ": {"AdvancedDMZ": self.dmz},
        }
        return values.get(xpath)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/gui/js/gui-core.js":
            if not self.gui:
                return httpx.Response(404)
            flag = 1 if self.method == "sha512" else 0
            return httpx.Response(200, text=f"gui.opt = {{GUI_ACTIVATE_SHA512ENCODE_OPT: {flag}}};")
        body = json.loads(parse_qs(req.content.decode())["req"][0])["request"]
        cred = self.h(f"admin:{self.nonce if body['session-id'] else ''}:{self.h(self.password)}")
        expect = self.h(f"{cred}:{body['id']}:{body['cnonce']}:JSON:/cgi/json-req")
        reply = lambda err, actions=None: httpx.Response(  # noqa: E731
            200, json={"reply": {"error": {"description": err}, "actions": actions or []}}
        )
        if body["auth-key"] != expect:
            return reply("XMO_AUTHENTICATION_ERR")
        action = body["actions"][0]
        if action["method"] == "logIn":
            self.logins += 1
            self.session, self.nonce = 4242, f"n{self.logins}"
            return reply(
                "XMO_REQUEST_NO_ERR",
                [{"callbacks": [{"parameters": {"id": self.session, "nonce": self.nonce}}]}],
            )
        if self.expire_next:
            self.expire_next = False
            self.session = 0
            return reply("XMO_INVALID_SESSION_ERR")
        if body["session-id"] != self.session:
            return reply("XMO_INVALID_SESSION_ERR")
        out = []
        for a in body["actions"]:
            if a["method"] == "logOut":
                out.append({"callbacks": [{"parameters": {}}]})
                continue
            v = self.tree(unquote(a["xpath"]))
            out.append(
                {
                    "error": {
                        "description": "XMO_NO_ERR" if v is not None else "XMO_UNKNOWN_PATH_ERR"
                    },
                    "callbacks": [{"parameters": {"value": v}}],
                }
            )
        return reply(
            "XMO_REQUEST_ACTION_ERR"
            if any(
                o.get("error", {}).get("description") != "XMO_NO_ERR" and "error" in o for o in out
            )
            else "XMO_REQUEST_NO_ERR",
            out,
        )


def client_for(fake, password=PASSWORD) -> HubClient:
    return HubClient("192.168.2.1", password, transport=httpx.MockTransport(fake))


# ── client ────────────────────────────────────────────────────────────────────


async def test_snapshot_with_sha512():
    fake = FakeHub("sha512")
    c = client_for(fake)
    s = await c.snapshot()
    assert c.method == "sha512" and fake.logins == 1
    assert s["model"] == "Home Hub 4000" and s["uptime_s"] == 864_000
    assert s["optical"]["rx_dbm"] == -19.23 and s["optical"]["status"] == "Up"
    assert s["optical"]["tx_dbm"] == 2.1
    assert s["ppp"]["status"] == "Connected" and s["wan_ip"] == "74.12.34.56"
    await c.aclose()


async def test_md5_firmware_without_gui_hint():
    fake = FakeHub("md5", gui=False)
    c = client_for(fake)
    assert (await c.snapshot())["uptime_s"] == 864_000 and c.method == "md5"
    await c.aclose()


async def test_wrong_password_and_reauth():
    c = client_for(FakeHub(), "nope")
    with pytest.raises(HubLoginError, match="wrong admin password"):
        await c.snapshot()
    await c.aclose()
    fake = FakeHub()
    c = client_for(fake)
    await c.snapshot()
    fake.expire_next = True
    await c.snapshot()
    assert fake.logins == 2
    await c.aclose()


async def test_exposure_reads_forwards_upnp_and_dmz():
    fake = FakeHub()
    c = client_for(fake)
    e = await c.exposure()
    assert e["upnp_enabled"] is True and e["dmz_host"] is None and e["dmz_known"]
    plex, xbox = e["port_mappings"]
    assert plex["origin"] == "manual" and plex["internal_ip"] == "192.168.2.15"
    assert xbox["origin"] == "upnp" and xbox["protocol"] == "UDP"
    fake.dmz = {"Enable": True, "AdvancedDMZhost": "192.168.2.50"}
    assert (await c.exposure())["dmz_host"] == "192.168.2.50"
    await c.aclose()


async def test_explore_hides_secrets():
    c = client_for(FakeHub())
    v = await c.explore("Device/WiFi/AccessPoints")
    assert v[0]["Security"]["KeyPassphrase"] == "••••" and v[0]["SSID"] == "home"
    assert await c.explore("Device/Nope") is None
    await c.aclose()


def test_optical_units():
    assert optical_dbm(-19234) == -19.23  # TR-181 thousandths
    assert optical_dbm(-19.5) == -19.5  # already dBm
    assert optical_dbm(120) == pytest.approx(-19.21, abs=0.01)  # 0.1 µW
    assert optical_dbm(2100, tx=True) == 2.1 and optical_dbm(2.5, tx=True) == 2.5
    assert optical_dbm(15849, tx=True) == pytest.approx(2.0, abs=0.01)  # 0.1 µW
    assert optical_dbm(0) is None and optical_dbm("x") is None and optical_dbm(-127500) is None
    assert redact({"PreSharedKey": "x", "Name": "y", "Sub": [{"Password": "z"}]}) == {
        "PreSharedKey": "••••",
        "Name": "y",
        "Sub": [{"Password": "••••"}],
    }


# ── service ───────────────────────────────────────────────────────────────────


@pytest.fixture
def svc(store, tmp_path):
    from netwatch.services.events import EventLog

    fake = FakeHub()
    s = HubService(
        store,
        EventLog(store.engine),
        HubStore(store.engine),
        SecretBox(tmp_path / "data"),
        client_factory=lambda h, p: client_for(fake, p),
    )
    s.save("192.168.2.1", True, PASSWORD)
    s.fake = fake
    return s


def hub_events(svc):
    return svc.events.list(0, 10**13, kinds=[HUB])


async def test_restart_link_down_reconnect_and_weak_signal(svc):
    f, now = svc.fake, 2_000_000_000_000
    assert await svc.poll(now)
    f.uptime, f.ppp_uptime = 864_060, 50_060
    await svc.poll(now + 60_000)
    assert hub_events(svc) == []
    f.uptime, f.ppp_uptime = 864_120, 20  # session restarted, Hub stayed up
    await svc.poll(now + 120_000)
    f.optical_status, f.ppp_status = "Down", "Disconnected"
    await svc.poll(now + 180_000)
    f.optical_status, f.ppp_status = "Up", "Connected"
    f.uptime, f.ppp_uptime = 45, 30  # and then the Hub restarted
    f.rx_raw = -27800
    await svc.poll(now + 240_000)
    by = {e["details"]["type"]: e for e in hub_events(svc)}
    assert by["ppp_reconnect"]["start_ms"] == now + 120_000 - 20_000
    assert (
        "Fibre link down" in by["wan_down"]["title"] and by["wan_down"]["end_ms"] == now + 240_000
    )
    assert by["hub_restart"]["start_ms"] == now + 240_000 - 45_000
    assert "after 10 d 0 h" in by["hub_restart"]["title"]
    assert by["weak_signal"]["details"]["rx_dbm"] == -27.8
    assert len(svc.hub_store.history(0, 10**13)) == 5


async def test_sign_in_failures_pause_polling(svc):
    svc.fake.password = "changed-on-the-hub"
    for _ in range(3):
        assert not await svc.poll()
    assert svc.paused and "wrong admin password" in svc.error
    svc.save("192.168.2.1", True, "changed-on-the-hub")
    assert svc.paused is None


# ── API, diagnosis, report ────────────────────────────────────────────────────


def test_admin_api(client):
    st = client.app.state
    fake = FakeHub()
    st.hub.client_factory = lambda h, p: client_for(fake, p)
    assert (
        client.put("/api/admin/hub", json={"host": "8.8.8.8", "enabled": True}).status_code == 422
    )
    r = client.post("/api/admin/hub/test", json={"host": "192.168.2.1", "password": PASSWORD})
    assert r.json()["ok"] and r.json()["snapshot"]["optical"]["rx_dbm"] == -19.23
    r = client.put(
        "/api/admin/hub", json={"host": "192.168.2.1", "enabled": True, "password": PASSWORD}
    )
    assert r.json()["password_set"] and PASSWORD not in r.text
    assert client.post("/api/admin/hub/test", json={"host": "192.168.2.9"}).status_code == 422
    r = client.post("/api/admin/hub/explore", json={"xpath": "Device/WiFi/AccessPoints"})
    assert r.json()["value"][0]["Security"]["KeyPassphrase"] == "••••"
    assert client.post("/api/admin/hub/explore", json={"xpath": "../etc"}).status_code == 422
    client.portal.call(st.hub.poll)
    g = client.get("/api/map").json()
    hub = next(n for n in g["nodes"] if n["id"] == "hub")
    assert hub["info"]["model"] == "Home Hub 4000" and hub["info"]["rx_dbm"] == -19.23


def test_diagnosis_and_report_use_hub_evidence(client):
    from .conftest import make_target

    st = client.app.state
    cf = make_target(st.store, name="Cloudflare", host="1.1.1.1", group="public")
    goog = make_target(st.store, name="Google", host="8.8.8.8", group="public")
    start = int(time.time() * 1000) - 600_000
    for t in (cf, goog):
        st.events.open(
            OUTAGE,
            "critical",
            start,
            f"{t['name']} unreachable",
            target_id=t["id"],
            end_ms=start + 120_000,
        )
    st.events.instant(
        HUB,
        "warning",
        start - 20_000,
        "Home Hub restarted after 3 d 2 h",
        details={"type": "hub_restart"},
    )
    inc = client.get("/api/diagnosis").json()["incidents"][0]
    assert inc["causes"][0]["id"] == "hub_restart_confirmed"
    assert inc["hub_events"][0]["type"] == "hub_restart"
    st.hub_store.add(start, {"uptime_s": 10, "optical": {"rx_dbm": -20.5}})
    rep = client.get(f"/api/report?start={start // 1000 - 60}").json()
    assert rep["line"]["hub_restarts"] == 1 and rep["line"]["rx_dbm_min"] == -20.5
    assert "Home Hub restarts: 1" in rep["markdown"]
    assert HubError
