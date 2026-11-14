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
