from __future__ import annotations

import time

from .frames import arp, dhcp_client_frame, dhcp_server_frame

DECO = "3c:84:6a:00:00:02"
PC = "3c:52:82:aa:bb:cc"
PHONE_A = "02:11:22:33:44:55"
PHONE_B = "06:aa:bb:cc:dd:ee"


def feed(client, *frames, outgoing=False):
    st = client.app.state
    now = int(time.time() * 1000)
    for f in frames:
        st.discovery.queue.put(now, f, outgoing)
    # process on the app's own event loop, like the background drain task does
    return client.portal.call(st.discovery.process_pending)


def device_id(client, mac):
    rows = client.get("/api/devices").json()["devices"]
    return next(r["id"] for r in rows if mac in r["macs"])


def test_devices_require_login(anon_client):
    assert anon_client.get("/api/devices").status_code == 401
    assert anon_client.get("/api/discovery").status_code == 401


def test_inventory_listing_and_edit(client):
    feed(
        client,
        dhcp_client_frame(DECO, 3, options={12: b"Deco-X55"}),
        arp(1, DECO, "192.168.2.10", "192.168.2.1"),
        arp(1, PC, "192.168.2.20", "192.168.2.1"),
    )
    body = client.get("/api/devices").json()
    labels = {d["label"] for d in body["devices"]}
    assert "Deco-X55" in labels and len(body["devices"]) == 2
    assert client.get("/api/devices", params={"q": "deco"}).json()["devices"][0]["mac"] == DECO

    did = device_id(client, PC)
    r = client.patch(f"/api/devices/{did}", json={"name": "Office PC", "type_override": "computer"})
    assert r.status_code == 200, r.text
    dev = r.json()["device"]
    assert dev["label"] == "Office PC" and dev["type"] == "computer"
    assert r.json()["ips"][0]["ip"] == "192.168.2.20"
    assert client.patch(f"/api/devices/{did}", json={"type_override": "toaster"}).status_code == 422


def test_monitor_follows_ip_changes(client):
    feed(client, arp(1, PC, "192.168.2.20", "192.168.2.1"))
    did = device_id(client, PC)
    r = client.post(f"/api/devices/{did}/monitor")
    assert r.status_code == 201, r.text
    target = r.json()["target"]
    assert target["host"] == "192.168.2.20" and target["group"] == "device"
    assert client.post(f"/api/devices/{did}/monitor").status_code == 409

    feed(client, arp(1, PC, "192.168.2.21", "192.168.2.1"))
    assert client.get(f"/api/targets/{target['id']}").json()["host"] == "192.168.2.21"
    moved = client.get("/api/events", params={"kind": "device"}).json()
    assert any("moved from 192.168.2.20 to 192.168.2.21" in e["title"] for e in moved)

    assert client.delete(f"/api/devices/{did}").status_code == 409  # monitored
    r = client.delete(f"/api/devices/{did}/monitor")
    assert r.status_code == 200 and r.json()["target"] is None
    assert client.get(f"/api/targets/{target['id']}").status_code == 404
    assert client.delete(f"/api/devices/{did}").status_code == 204


def test_merge_and_unmerge(client):
    feed(
        client,
        arp(1, PHONE_A, "192.168.2.40", "192.168.2.1"),
        arp(1, PHONE_B, "192.168.2.41", "192.168.2.1"),
    )
    a, b = device_id(client, PHONE_A), device_id(client, PHONE_B)
    r = client.post(f"/api/devices/{b}/merge", json={"into": a})
    assert r.status_code == 200, r.text
    assert r.json()["device"]["macs"] == [PHONE_A, PHONE_B]
    assert len(client.get("/api/devices").json()["devices"]) == 1
    assert client.post(f"/api/devices/{a}/merge", json={"into": b}).status_code == 409
    assert client.post(f"/api/devices/{b}/unmerge").status_code == 200
    assert len(client.get("/api/devices").json()["devices"]) == 2


def test_outgoing_probe_does_not_create_phantom_device(client):
    feed(client, dhcp_client_frame("02:de:ad:be:ef:01", 1), outgoing=True)
    assert client.get("/api/devices").json()["devices"] == []


def test_new_device_events_after_learning_period(client):
    st = client.app.state
    st.store.kv_set("discovery_learning_until_ms", 0)
    feed(
        client,
        arp(1, PC, "192.168.2.20", "192.168.2.1"),
        arp(1, PHONE_A, "192.168.2.40", "192.168.2.1"),
    )
    evs = client.get("/api/events", params={"kind": "device"}).json()
    assert [e["details"]["mac"] for e in evs] == [PC]  # private MACs are not announced


def test_anomaly_reaches_timeline_and_device(client):
    rogue = "3c:84:6a:00:00:09"
    feed(
        client,
        dhcp_server_frame("a0:b5:49:00:00:01", "192.168.2.1", PHONE_A, 2),
        dhcp_server_frame(rogue, "192.168.68.1", PHONE_A, 2),
    )
    evs = client.get("/api/events", params={"kind": "anomaly"}).json()
    assert evs[0]["details"]["type"] == "dhcp_server"
    detail = client.get(f"/api/devices/{device_id(client, rogue)}").json()
    assert detail["events"][0]["id"] == evs[0]["id"]
    status = client.get("/api/discovery").json()
    assert {s["ip"] for s in status["dhcp_servers"]} == {"192.168.2.1", "192.168.68.1"}


def test_discovery_controls(client):
    s = client.get("/api/discovery").json()
    assert s["active"]["enabled"] is False and s["passive"]["running"] is False
    s = client.put("/api/discovery", json={"active": True}).json()
    assert s["active"]["enabled"] is True
    # no default route / interface in the test environment: scanning refuses cleanly
    r = client.post("/api/discovery/scan")
    assert r.status_code == 409 and "IPv4" in r.json()["detail"]


def test_dns_check_endpoint(client):
    async def fake(resolver, name, rdtype):
        if name.startswith("netwatch-"):
            return ("ok", ["67.63.55.3"]) if resolver == "192.0.2.53" else ("nxdomain", [])
        return "ok", ["142.250.1.1"]

    st = client.app.state
    st.discovery.dns_query = fake
    st.store.create_target(
        {"name": "dns", "kind": "dns", "host": "192.0.2.53", "dns_query": "www.google.com",
         "dns_rdtype": "A", "interval_s": 10, "timeout_s": 3, "enabled": True, "group": "dns",
         "auto": False}
    )  # fmt: skip
    r = client.post("/api/discovery/dns-check")
    assert r.status_code == 200
    assert r.json()["resolvers"] == ["192.0.2.53"]
    assert any("non-existent" in f for f in r.json()["findings"])
