from __future__ import annotations

import pytest

from netwatch.services.device_groups import GroupError, GroupStore, clean_tag
from netwatch.services.discovery.inventory import DeviceStore


@pytest.fixture
def gs(store):
    return GroupStore(store.engine)


@pytest.fixture
def devs(store):
    return DeviceStore(store.engine)


def add(devs, mac, **kw):
    return devs.insert({"mac": mac, "first_seen_ms": 0, "last_seen_ms": 0, **kw})


def test_schema_has_groups_and_tags(store):
    from sqlalchemy import inspect

    insp = inspect(store.engine)
    assert {c["name"] for c in insp.get_columns("devices")} >= {"group_id"}
    assert "device_groups" in insp.get_table_names() and "device_tags" in insp.get_table_names()


def test_group_crud_and_unique_names(gs, devs):
    kids = gs.create("Kids", "rose", "child")
    assert kids["name"] == "Kids" and kids["sort"] == 1
    with pytest.raises(GroupError, match="already a group called Kids"):
        gs.create("  kids ", "sky", "home")
    for bad in (("", "sky", "home"), ("X" * 41, "sky", "home"), ("A", "red!", "home"),
                ("A", "sky", "rocket")):  # fmt: skip
        with pytest.raises(GroupError):
            gs.create(*bad)
    home = gs.create("Smart home", "emerald", "plug")
    assert [g["name"] for g in gs.list()] == ["Kids", "Smart home"]
    assert gs.update(home["id"], name="Smart  home ", icon="home")["name"] == "Smart home"
    with pytest.raises(GroupError):
        gs.update(home["id"], name="KIDS")
    d = add(devs, "aa:00:00:00:00:01")
    gs.assign([d], kids["id"])
    assert next(g for g in gs.list() if g["name"] == "Kids")["devices"] == 1
    assert gs.delete(kids["id"]) == 1
    assert devs.get(d)["group_id"] is None
    with pytest.raises(GroupError):
        gs.delete(kids["id"])


def test_assign_and_tags_land_on_the_primary(gs, devs):
    g = gs.create("Family devices", "sky", "family")
    phone = add(devs, "aa:00:00:00:00:10")
    private = add(devs, "da:00:00:00:00:11", merged_into=phone)
    assert gs.assign([private], g["id"]) == 1
    assert devs.get(phone)["group_id"] == g["id"] and devs.get(private)["group_id"] is None
    gs.add_tags([private, phone], ["Upstairs", "work"])
    assert gs.tags_of([phone]) == {phone: ["upstairs", "work"]}
    gs.remove_tags([phone], ["work"])
    assert gs.set_tags(phone, ["a", "b", "a"]) == ["a", "b"]
    assert gs.tags() == [{"tag": "a", "devices": 1}, {"tag": "b", "devices": 1}]
    for bad in ("", "-x", "x" * 25, "a_b", "é"):
        with pytest.raises(GroupError):
            clean_tag(bad)
    with pytest.raises(GroupError, match="at most 12"):
        gs.set_tags(phone, [f"t{i}" for i in range(13)])
    with pytest.raises(GroupError, match="no such device"):
        gs.assign([99999], g["id"])
    with pytest.raises(GroupError, match="no such group"):
        gs.assign([phone], 4242)
    gs.assign([phone], None)
    assert devs.get(phone)["group_id"] is None


def test_purge_keeps_grouped_and_tagged_devices(gs, devs):
    g = gs.create("Smart home", "emerald", "plug")
    grouped = add(devs, "aa:00:00:00:00:20")
    tagged = add(devs, "aa:00:00:00:00:21")
    plain = add(devs, "aa:00:00:00:00:22")
    gs.assign([grouped], g["id"])
    gs.add_tags([tagged], ["garage"])
    assert devs.purge(10**12) == 1
    assert devs.get(plain) is None and devs.get(grouped) and devs.get(tagged)


def test_suggestions_by_type_and_accept(gs, devs):
    plug = add(devs, "aa:00:00:00:00:30", type="iot", hostname="kasa-plug")
    cam = add(devs, "aa:00:00:00:00:31", type="camera")
    add(devs, "aa:00:00:00:00:32", type="phone", name="Pixel 9")
    add(devs, "aa:00:00:00:00:33", type="unknown")
    s = {x["name"]: x for x in gs.suggestions()}
    assert set(s) == {"Smart home", "Family devices"}
    assert {d["id"] for d in s["Smart home"]["devices"]} == {plug, cam}
    assert s["Smart home"]["group_id"] is None and s["Smart home"]["colour"] == "emerald"
    g = gs.accept("Smart home", [plug])
    assert g["icon"] == "plug" and devs.get(plug)["group_id"] == g["id"]
    s = {x["name"]: x for x in gs.suggestions()}
    assert [d["id"] for d in s["Smart home"]["devices"]] == [cam]
    assert s["Smart home"]["group_id"] == g["id"]


def test_export_import_round_trip_by_mac(store, gs, devs):
    g = gs.create("Kids", "rose", "child")
    a = add(devs, "aa:00:00:00:00:40")
    b = add(devs, "aa:00:00:00:00:41")
    gs.assign([a], g["id"])
    gs.add_tags([b], ["guest"])
    data = gs.export()
    assert data["netwatch"] == "device-groups" and data["groups"][0]["name"] == "Kids"
    assert {d["mac"] for d in data["devices"]} == {"aa:00:00:00:00:40", "aa:00:00:00:00:41"}
    # A rebuilt database: same devices (by MAC), no groups yet, plus one MAC it never saw.
    gs.delete(g["id"])
    gs.set_tags(b, [])
    data["devices"].append({"mac": "aa:00:00:00:00:99", "group": "Kids", "tags": []})
    r = gs.import_(data)
    assert r == {"groups_created": 1, "devices": 2, "unknown_macs": ["aa:00:00:00:00:99"]}
    assert gs.by_name("kids")["id"] == devs.get(a)["group_id"]
    assert gs.tags_of([b]) == {b: ["guest"]}
    with pytest.raises(GroupError):
        gs.import_({"something": "else"})


# ── API ──────────────────────────────────────────────────────────────────────


def test_groups_api_bulk_filters_and_audit(client):
    from netwatch.services.events import DEVICE, SECURITY

    st = client.app.state
    plug = add(st.device_store, "aa:00:00:00:00:50", type="iot", hostname="kasa-plug",
               last_seen_ms=10**13)  # fmt: skip
    cam = add(st.device_store, "aa:00:00:00:00:51", type="camera", last_seen_ms=10**13)
    phone = add(st.device_store, "aa:00:00:00:00:52", type="phone", name="Pixel 9")
    r = client.get("/api/device-groups").json()
    assert r["groups"] == [] and "emerald" in r["colours"] and "child" in r["icons"]
    assert client.post("/api/device-groups", json={"name": "Kids", "colour": "red",
                                                   "icon": "child"}).status_code == 422  # fmt: skip
    kids = client.post("/api/device-groups", json={"name": "Kids", "colour": "rose",
                                                   "icon": "child"}).json()  # fmt: skip
    dup = client.post(
        "/api/device-groups", json={"name": "kids", "colour": "rose", "icon": "child"}
    )
    assert dup.status_code == 422 and "already" in dup.json()["detail"]
    # Suggestions, then accept part of one.
    s = {x["name"]: x for x in client.get("/api/device-groups/suggestions").json()}
    assert {d["id"] for d in s["Smart home"]["devices"]} == {plug, cam}
    accept = {"group_name": "Smart home", "device_ids": [plug, cam]}
    home = client.post("/api/device-groups/suggestions/accept", json=accept).json()
    # Bulk: set group, add and remove tags; 0 removes from the group.
    b = client.post("/api/devices/bulk", json={"ids": [phone], "group_id": kids["id"],
                                               "add_tags": ["Upstairs", "school"]})  # fmt: skip
    assert b.status_code == 200
    client.post("/api/devices/bulk", json={"ids": [phone], "remove_tags": ["school"]})
    assert (
        client.post(
            "/api/devices/bulk", json={"ids": [phone], "add_tags": ["bad tag!"]}
        ).status_code
        == 422
    )
    assert client.post("/api/devices/bulk", json={"ids": [], "group_id": 1}).status_code == 422
    assert client.post("/api/devices/bulk", json={"ids": [phone], "x": 1}).status_code == 422
    listing = {d["id"]: d for d in client.get("/api/devices").json()["devices"]}
    assert listing[phone]["group"]["name"] == "Kids" and listing[phone]["tags"] == ["upstairs"]
    assert listing[plug]["group"]["colour"] == "emerald"
    only_home = client.get(f"/api/devices?group_id={home['id']}").json()["devices"]
    assert {d["id"] for d in only_home} == {plug, cam}
    assert [d["id"] for d in client.get("/api/devices?tag=upstairs").json()["devices"]] == [phone]
    assert client.get("/api/devices?group_id=0").json()["devices"] == []
    assert client.get("/api/device-tags").json() == [{"tag": "upstairs", "devices": 1}]
    # Single-device edit, and the detail page shows both.
    r = client.patch(f"/api/devices/{plug}", json={"tags": ["garage"], "group_id": kids["id"]})
    assert r.status_code == 200
    d = client.get(f"/api/devices/{plug}").json()["device"]
    assert d["group"]["name"] == "Kids" and d["tags"] == ["garage"]
    client.post("/api/devices/bulk", json={"ids": [plug], "group_id": 0})
    assert client.get(f"/api/devices/{plug}").json()["device"]["group"] is None
    # Timeline filtered by group: matches by device id or MAC in the details.
    now = 1_700_000_000_000
    st.events.instant(DEVICE, "info", now, "Pixel 9 joined", details={"device_id": phone})
    st.events.instant(DEVICE, "info", now, "Camera moved", details={"mac": "aa:00:00:00:00:51"})
    st.events.instant(DEVICE, "info", now, "Someone else", details={"mac": "bb:00:00:00:00:00"})
    t = client.get(f"/api/events?start={now // 1000 - 10}&group_id={kids['id']}").json()
    assert [e["title"] for e in t] == ["Pixel 9 joined"]
    t = client.get(f"/api/events?start={now // 1000 - 10}&group_id={home['id']}").json()
    assert [e["title"] for e in t] == ["Camera moved"]
    # Export, delete, import.
    exp = client.get("/api/device-groups/export")
    assert exp.headers["content-disposition"].startswith("attachment") and exp.json()["groups"]
    assert client.delete(f"/api/device-groups/{kids['id']}").json() == {"ungrouped": 1}
    assert client.delete(f"/api/device-groups/{kids['id']}").status_code == 404
    imp = client.post("/api/device-groups/import", json=exp.json()).json()
    assert imp["groups_created"] == 1 and imp["unknown_macs"] == []
    assert client.post("/api/device-groups/import", json={"x": 1}).status_code == 422
    titles = " | ".join(e["title"] for e in st.events.list(0, 10**15, kinds=[SECURITY]))
    for want in ("Device group created: Kids", "Device group deleted: Kids (1 device ungrouped)",
                 "Device groups imported: 1 new groups"):  # fmt: skip
        assert want in titles, want


def test_groups_api_needs_sign_in(anon_client):
    for path in ("/api/device-groups", "/api/device-tags", "/api/device-groups/export"):
        assert anon_client.get(path).status_code == 401
