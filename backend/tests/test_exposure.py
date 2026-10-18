from __future__ import annotations

import time

import httpx

from netwatch.services.events import EXPOSURE, EventLog
from netwatch.services.exposure import ExposureScanner, ExposureStore, findings
from netwatch.services.hub.client import HubClient

from .test_hub import PASSWORD, FakeHub


def add_device(store, mac: str, ip: str, **over) -> int:
    from netwatch.services.discovery.inventory import DeviceStore

    now = int(time.time() * 1000)
    return DeviceStore(store.engine).insert(
        {"mac": mac, "last_ip": ip, "first_seen_ms": now, "last_seen_ms": now, **over}
    )


class FakeHubService:
    def __init__(self) -> None:
        self.fake = FakeHub()

    async def exposure(self):
        c = HubClient("192.168.2.1", PASSWORD, transport=httpx.MockTransport(self.fake))
        try:
            return await c.exposure()
        finally:
            await c.aclose()


def make_probe(open_ports: dict[str, set[int]]):
    async def probe(ip: str, port: int):
        return port in open_ports.get(ip, set()), "SSH-2.0-OpenSSH_9.6" if port == 22 else ""

    return probe


async def test_scan_findings_and_change_events(store):
    nas = add_device(store, "aa:00:00:00:00:15", "192.168.2.15", name="TrueNAS")
    cam = add_device(store, "aa:00:00:00:00:30", "192.168.2.30", name="Garage camera")
    add_device(store, "aa:00:00:00:00:99", "8.8.8.8")  # never scanned: not a LAN address
    old = add_device(store, "aa:00:00:00:00:31", "192.168.2.31")
    from netwatch.services.discovery.inventory import DeviceStore

    DeviceStore(store.engine).update(old, {"last_seen_ms": 0})  # not seen recently
    open_ports = {"192.168.2.15": {22, 445, 32400}, "192.168.2.30": {80, 554}}
    events = EventLog(store.engine)
    hub = FakeHubService()
    sc = ExposureScanner(
        ExposureStore(store.engine), store.engine, events, hub=hub, probe=make_probe(open_ports)
    )
    s = await sc.run("manual")
    assert s["latest"]["hosts"] == 2 and s["latest"]["open_ports"] == 5
    by = {(x["device_id"], x["port"]): x for x in s["services"]}
    assert by[(nas, 22)]["banner"] == "SSH-2.0-OpenSSH_9.6" and by[(nas, 445)]["risk"] == "high"
    plex = next(f for f in s["forwards"] if f["external_port"] == 32400)
    assert plex["device_id"] == nas and plex["device_label"] == "TrueNAS"
    titles = [f["title"] for f in s["findings"]]
    assert any("forwarded to TrueNAS — Plex" in t for t in titles)
    assert any("UPnP" in t for t in titles)
    assert not any("SMB (445) is open" in t for t in titles)  # SMB on a NAS is fine on the LAN
    assert events.list(0, 10**13, kinds=[EXPOSURE]) == []  # first scan = baseline

    # Something changes: telnet appears on the camera, SSH goes away, a new UPnP forward.
    open_ports["192.168.2.30"].add(23)
    open_ports["192.168.2.15"].discard(22)
    hub.fake.mappings.append(
        {"Enable": True, "Protocol": "TCP", "ExternalPort": 8080, "InternalPort": 80,
         "InternalClient": "192.168.2.30", "Creator": "UPNP"}
    )  # fmt: skip
    s = await sc.run("scheduled")
    assert (nas, 22) not in {(x["device_id"], x["port"]) for x in s["services"]}
    ev = {e["details"]["type"]: e for e in events.list(0, 10**13, kinds=[EXPOSURE])}
    assert ev["new_service"]["title"] == "New service on Garage camera: Telnet (23)"
    assert ev["new_service"]["severity"] == "warning"
    assert "through UPnP for Garage camera" in ev["new_forward"]["title"]
    assert any("Telnet (23) is open on Garage camera" in f["title"] for f in s["findings"])
    assert cam


def test_dmz_is_critical():
    f = findings([], [], {"dmz_host": "192.168.2.50", "upnp_enabled": False})
    assert f[0]["severity"] == "critical" and "DMZ" in f[0]["title"]


def test_api(client):
    st = client.app.state
    add_device(st.store, "aa:00:00:00:00:15", "192.168.2.15")
    st.exposure.probe = make_probe({"192.168.2.15": {80}})
    assert client.get("/api/exposure").json()["latest"] is None
    r = client.post("/api/exposure/scan")
    assert r.status_code == 202
    deadline = time.time() + 5
    while time.time() < deadline:
        body = client.get("/api/exposure").json()
        if body["latest"] and not body["running"]:
            break
        time.sleep(0.05)
    assert body["latest"]["open_ports"] == 1 and body["hub_read"] is False
    assert body["services"][0]["service"] == "HTTP" and body["nightly"] is True
