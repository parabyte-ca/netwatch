from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from netwatch import db
from netwatch.db import make_engine, run_migrations
from netwatch.services.assets import AssetError, AssetStore, hints
from netwatch.services.events import SECURITY

DECO = "aa:bb:cc:00:00:01"


def add_device(st, mac, name, ip="192.168.2.50", **extra) -> int:
    row = {
        "mac": mac,
        "first_seen_ms": 0,
        "last_seen_ms": int(time.time() * 1000),
        "last_ip": ip,
        "name": name,
    }
    return st.discovery.inventory.store.insert({**row, **extra})


def graph(client) -> dict:
    return client.get("/api/map").json()


def node(g, nid):
    return next(n for n in g["nodes"] if n["id"] == nid)


def edge(g, source, target):
    return next((e for e in g["edges"] if e["source"] == source and e["target"] == target), None)


@pytest.fixture
def store(tmp_path):
    engine = make_engine(tmp_path / "a.db")
    run_migrations(engine)
    return AssetStore(engine)


# ── migration ────────────────────────────────────────────────────────────────


def _alembic(engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(db.__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


def test_migration_makes_the_inferred_switch_the_first_asset_and_keeps_its_layout(tmp_path):
    engine = make_engine(tmp_path / "m.db")
    cfg = _alembic(engine)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0014")
    layout = {
        "positions": {"switch": {"x": 5, "y": 6}, "hub": {"x": 1, "y": 2}},
        "pins": {"switch": "left"},
    }
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO kv (key, value) VALUES ('map_layout', :v)"), {"v": json.dumps(layout)}
        )
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert {"assets", "node_parents"} <= set(insp.get_table_names())
    with engine.connect() as conn:
        a = conn.execute(text("SELECT id, kind, name, uplink FROM assets")).all()
        links = conn.execute(text("SELECT node_key, parent FROM node_parents")).all()
        saved = json.loads(
            conn.execute(text("SELECT value FROM kv WHERE key='map_layout'")).scalar()
        )
    assert [tuple(r) for r in a] == [(1, "switch", "Switch", "hub")]
    assert [tuple(r) for r in links] == [("truenas", "asset:1")]
    assert saved["positions"] == {"asset:1": {"x": 5, "y": 6}, "hub": {"x": 1, "y": 2}}
    assert saved["pins"] == {"asset:1": "left"}
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0014")
    with engine.connect() as conn:
        back = json.loads(
            conn.execute(text("SELECT value FROM kv WHERE key='map_layout'")).scalar()
        )
    assert "switch" in back["positions"] and "asset:1" not in back["positions"]
    assert not {"assets", "node_parents"} & set(inspect(engine).get_table_names())
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    run_migrations(engine)  # idempotent at head


# ── store ────────────────────────────────────────────────────────────────────


def test_create_validates_names_uplinks_and_the_limit(store):
    assert [a["name"] for a in store.list()] == ["Switch"]
    a = store.create("  Office   switch ", "hub", "behind the desk")
    assert a["name"] == "Office switch" and a["uplink"] == "hub" and a["notes"] == "behind the desk"
    for bad in ("", "x" * 41, "office SWITCH"):
        with pytest.raises(AssetError):
            store.create(bad)
    for uplink in ("lan", "asset:999", "deco:zz", "asset:"):
        with pytest.raises(AssetError):
            store.create("Other", uplink)
    deco = store.create("On a Deco", f"deco:{DECO}")
    chained = store.create("Chained", f"asset:{deco['id']}")
    assert chained["uplink"] == f"asset:{deco['id']}"
    for i in range(30 - len(store.list())):
        store.create(f"Filler {i}")
    with pytest.raises(AssetError, match="at most 30"):
        store.create("One too many")


def test_a_switch_cannot_plug_into_itself_or_its_own_chain(store):
    a = store.create("A")
    b = store.create("B", f"asset:{a['id']}")
    c = store.create("C", f"asset:{b['id']}")
    with pytest.raises(AssetError, match="loop"):
        store.update(a["id"], {"uplink": f"asset:{a['id']}"})
    with pytest.raises(AssetError, match="loop"):
        store.update(a["id"], {"uplink": f"asset:{c['id']}"})
    assert store.update(c["id"], {"uplink": "hub"})["uplink"] == "hub"


def test_update_changes_only_what_is_given(store):
    a = store.create("A", "hub", "n")
    assert store.update(a["id"], {"notes": ""})["notes"] is None
    assert store.update(a["id"], {"name": "A2"})["name"] == "A2"
    with pytest.raises(AssetError):
        store.update(a["id"], {"name": "switch"})  # the migrated "Switch" already exists
    with pytest.raises(AssetError, match="no such"):
        store.update(999, {"name": "x"})


def test_assign_validates_and_unpins(store):
    a = store.create("A")
    assert store.assign(["dev:5", "dev:6", "dev:5"], f"asset:{a['id']}") == 2
    assert store.assign(["dev:5"], f"asset:{a['id']}") == 0  # already there
    assert store.links()["dev:5"] == f"asset:{a['id']}"
    assert store.get(a["id"])["members"] == ["dev:5", "dev:6"]
    assert store.assign(["dev:5"], f"deco:{DECO}") == 1  # moved, not duplicated
    assert store.assign(["dev:6"], None) == 1
    assert "dev:6" not in store.links()
    for keys, parent in (
        (["dev:x"], "asset:1"),
        (["hub"], "asset:1"),
        (["dev:5"], "asset:999"),
        (["dev:5"], "lan"),
    ):
        with pytest.raises(AssetError):
            store.assign(keys, parent)


def test_deleting_a_switch_hands_what_was_behind_it_to_its_uplink(store):
    first = store.get(1)
    a = store.create("A", "hub")
    b = store.create("B", f"asset:{a['id']}")
    store.assign(["dev:1", "dev:2"], f"asset:{a['id']}")
    store.assign(["dev:3"], f"asset:{b['id']}")
    store.delete(a["id"])
    # A plugged into the Hub: its devices simply lose the pin (the Hub is the default), and B,
    # which plugged into A, now plugs into the Hub.
    assert "dev:1" not in store.links() and "dev:2" not in store.links()
    assert store.get(b["id"])["uplink"] == "hub" and store.links()["dev:3"] == f"asset:{b['id']}"
    d = store.create("D", f"deco:{DECO}")
    e = store.create("E", f"asset:{d['id']}")
    store.assign(["dev:4"], f"asset:{e['id']}")
    store.delete(e["id"])
    assert store.links()["dev:4"] == f"asset:{d['id']}"
    store.delete(d["id"])
    assert store.links()["dev:4"] == f"deco:{DECO}"
    assert first["name"] == "Switch" and store.get(1) is not None


def test_a_deleted_or_merged_device_takes_its_pin_along(store):
    store.assign(["dev:7", "dev:8"], "asset:1")
    store.forget_device(7)
    assert "dev:7" not in store.links()
    store.move_pin(8, 9)
    assert store.links().get("dev:9") == "asset:1" and "dev:8" not in store.links()
    store.assign(["dev:10"], f"deco:{DECO}")
    store.assign(["dev:11"], "asset:1")
    store.move_pin(11, 10)  # the survivor keeps its own pin
    assert store.links()["dev:10"] == f"deco:{DECO}" and "dev:11" not in store.links()


# ── hints ────────────────────────────────────────────────────────────────────


def row(i, label, *, online=True, deco_mac=None, connection=None, **over):
    details = {}
    if deco_mac:
        details["deco_mac"] = deco_mac
    if connection:
        details["connection"] = connection
    return {
        "id": i,
        "label": label,
        "online": online,
        "details": details,
        "type": "computer",
        **over,
    }


def test_hints_list_unclaimed_devices_and_decos_with_several_wired_devices():
    decos = {DECO: {"name": "Office"}}
    rows = [
        row(1, "NAS"),  # no Deco claims it
        row(2, "Printer", deco_mac=DECO, connection="wired"),
        row(3, "TV", deco_mac=DECO, connection="wired"),
        row(4, "Console", deco_mac=DECO, connection="wired"),
        row(5, "Phone", deco_mac=DECO, connection="5 GHz"),
        row(6, "Old", online=False),
        row(7, "Router", type="router"),
        row(8, "Already pinned"),
        row(9, "Deco", details={"is_deco": True}),
    ]
    h = hints(rows, decos, {"dev:8": "asset:1"})
    # Not offline, not the router, not a Deco, not already pinned; Wi-Fi and wired Deco clients
    # are claimed by their Deco.
    assert [d["label"] for d in h["unclaimed"]] == ["NAS"]
    assert [(w["name"], [d["label"] for d in w["devices"]]) for w in h["decos"]] == [
        ("Deco Office", ["Console", "Printer", "TV"])
    ]
    assert h["decos"][0]["deco"] == f"deco:{DECO}"
    few = hints(rows[:3], decos, {})
    assert few["decos"] == []  # two wired devices fit the Deco's own ports


# ── the map ──────────────────────────────────────────────────────────────────


def test_the_first_switch_replaces_the_fixed_one_and_truenas_hangs_behind_it(client):
    g = graph(client)
    assert node(g, "asset:1")["label"] == "Switch" and node(g, "asset:1")["inferred"]
    assert not any(n["id"] == "switch" for n in g["nodes"])
    assert edge(g, "hub", "asset:1") and edge(g, "asset:1", "truenas")


def test_with_no_switches_the_fixed_inferred_one_comes_back(client):
    st = client.app.state
    st.assets.delete(1)
    g = graph(client)
    assert (
        node(g, "switch")["inferred"] and edge(g, "hub", "switch") and edge(g, "switch", "truenas")
    )
    assert not any(n["id"].startswith("asset:") for n in g["nodes"])


def test_devices_pinned_behind_a_switch_hang_under_it_on_a_cable_and_set_its_state(client):
    st = client.app.state
    nas = add_device(st, "aa:00:00:00:00:21", "NAS", ip="192.168.2.21")
    new = st.assets.create("Desk switch", "hub")
    key = f"asset:{new['id']}"
    assert node(graph(client), key)["state"] == "unknown"  # nothing behind it yet
    st.assets.assign([f"dev:{nas}"], key)
    g = graph(client)
    assert edge(g, key, f"dev:{nas}")["medium"] == "wired"
    assert edge(g, "hub", f"dev:{nas}") is None
    assert node(g, key)["state"] == "up"  # a device behind it is online
    assert node(g, f"dev:{nas}")["info"]["parent"] == key
    # TrueNAS can be moved out from behind the first switch, to the Hub.
    st.assets.assign(["truenas"], None)
    assert edge(graph(client), "hub", "truenas")


def test_switches_can_plug_into_a_deco_or_another_switch(client):
    st = client.app.state
    st.deco.decos = {
        DECO: {
            "name": "Office",
            "role": "main",
            "online": True,
            "ip": "192.168.2.10",
            "model": "X20",
            "backhaul": "wired",
            "firmware": "1",
        }
    }
    a = st.assets.create("On the Deco", f"deco:{DECO}")
    b = st.assets.create("Behind it", f"asset:{a['id']}")
    g = graph(client)
    assert edge(g, f"deco:{DECO}", f"asset:{a['id']}")["medium"] == "wired"
    assert edge(g, f"asset:{a['id']}", f"asset:{b['id']}")
    mac = "aa:00:00:00:00:31"
    dev = add_device(
        st, mac, "Camera", ip="192.168.2.31", details={"deco_mac": DECO, "connection": "wired"}
    )
    st.assets.assign([f"dev:{dev}"], f"asset:{b['id']}")
    assert edge(graph(client), f"asset:{b['id']}", f"dev:{dev}")
    st.assets.assign([f"dev:{dev}"], f"deco:{DECO}")  # pinned straight to a Deco port
    assert edge(graph(client), f"deco:{DECO}", f"dev:{dev}")["medium"] == "wired"


def test_a_pin_to_a_deco_that_is_gone_falls_back_to_the_automatic_parent(client):
    st = client.app.state
    dev = add_device(st, "aa:00:00:00:00:41", "Box", ip="192.168.2.41")
    st.assets.assign([f"dev:{dev}"], f"deco:{DECO}")  # no such Deco in the map
    assert edge(graph(client), "hub", f"dev:{dev}")


def test_the_diagnosis_and_the_report_ignore_switches(client):
    st = client.app.state
    st.assets.create("Extra", "hub")
    assert client.get("/api/diagnosis").status_code == 200
    topo = json.dumps(client.get("/api/diagnosis").json())
    assert "asset:" not in topo
    assert client.get("/api/map").status_code == 200


def test_a_truenas_link_cause_highlights_the_switch_it_is_behind(client):
    st = client.app.state
    extra = st.assets.create("Rack switch", "hub")
    st.assets.assign(["truenas"], f"asset:{extra['id']}")
    g = graph(client)
    inc = {"id": "i1", "start_ms": 0, "failing": [], "causes": [{"layer": "lan", "title": "x"}]}
    hl = st.netmap.highlight(inc, g)
    assert hl["cause_nodes"] == [f"asset:{extra['id']}"]


# ── API ──────────────────────────────────────────────────────────────────────


def test_the_api_needs_a_login_and_the_same_origin_header(anon_client, client):
    assert anon_client.get("/api/assets").status_code == 401
    assert (
        client.post("/api/assets", json={"name": "X"}, headers={"X-Requested-With": ""}).status_code
        == 403
    )
    assert client.delete("/api/assets/1", headers={"X-Requested-With": ""}).status_code == 403
    assert client.post("/api/assets/assign", json={"nodes": ["truenas"], "parent": None},
                       headers={"X-Requested-With": ""}).status_code == 403  # fmt: skip


def test_the_api_adds_changes_removes_and_logs(client):
    st = client.app.state
    body = client.get("/api/assets").json()
    assert [a["name"] for a in body["assets"]] == ["Switch"]
    assert body["assets"][0]["members"] == [{"key": "truenas", "label": "TrueNAS (NetWatch)"}]
    r = client.post(
        "/api/assets", json={"name": "Office", "uplink": "hub", "notes": "under the desk"}
    )
    assert r.status_code == 201, r.text
    aid = r.json()["asset"]["id"]
    assert client.post("/api/assets", json={"name": "office"}).status_code == 422  # same name
    assert (
        client.post("/api/assets", json={"name": "Bad", "uplink": "asset:999"}).status_code == 422
    )
    assert (
        client.post("/api/assets", json={"name": "Deco", "uplink": f"deco:{DECO}"}).status_code
        == 422
    )
    r = client.put(f"/api/assets/{aid}", json={"name": "Office rack", "notes": ""})
    assert r.status_code == 200 and r.json()["asset"]["name"] == "Office rack"
    assert client.put("/api/assets/999", json={"name": "x"}).status_code == 404
    assert client.put(f"/api/assets/{aid}", json={"uplink": f"asset:{aid}"}).status_code == 422
    assert client.put(f"/api/assets/{aid}", json={"nope": 1}).status_code == 422
    assert client.delete(f"/api/assets/{aid}").status_code == 200
    assert client.delete(f"/api/assets/{aid}").status_code == 404
    log = [e["title"] for e in st.events.list(0, 10**14, kinds=[SECURITY])]
    assert "Map: added the switch Office" in log
    assert "Map: changed the switch Office rack (name, notes)" in log
    assert any(t.startswith("Map: removed the switch Office rack") for t in log)


def test_the_api_assigns_devices_and_refuses_wifi_devices_behind_a_switch(client):
    st = client.app.state
    nas = add_device(
        st, "aa:00:00:00:00:51", "NAS", ip="192.168.2.51", details={"connection": "wired"}
    )
    phone = add_device(
        st, "aa:00:00:00:00:52", "Phone", ip="192.168.2.52", details={"connection": "5 GHz"}
    )
    sw = client.post("/api/assets", json={"name": "Rack"}).json()["asset"]["id"]
    url = "/api/assets/assign"
    ok = client.post(url, json={"nodes": [f"dev:{nas}"], "parent": f"asset:{sw}"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["pins"][f"dev:{nas}"] == f"asset:{sw}"
    wifi = client.post(url, json={"nodes": [f"dev:{phone}"], "parent": f"asset:{sw}"})
    assert wifi.status_code == 422 and "Wi-Fi" in wifi.json()["detail"]
    assert (
        client.post(url, json={"nodes": ["dev:9999"], "parent": f"asset:{sw}"}).status_code == 404
    )
    assert client.post(url, json={"nodes": ["hub"], "parent": f"asset:{sw}"}).status_code == 422
    assert (
        client.post(url, json={"nodes": [f"dev:{nas}"], "parent": f"deco:{DECO}"}).status_code
        == 422
    )
    assert (
        client.post(url, json={"nodes": [f"dev:{nas}"], "parent": "asset:999"}).status_code == 422
    )
    assert (
        client.post(url, json={"nodes": [f"dev:{nas}"], "parent": None})
        .json()["pins"]
        .get(f"dev:{nas}")
        is None
    )
    log = [e["title"] for e in st.events.list(0, 10**14, kinds=[SECURITY])]
    assert f"Map: 1 device pinned behind asset:{sw}" in log
    assert "Map: 1 device unpinned" in log


def test_deleting_or_merging_a_device_cleans_up_its_pin(client):
    st = client.app.state
    a = add_device(st, "aa:00:00:00:00:61", "A", ip="192.168.2.61")
    b = add_device(st, "aa:00:00:00:00:62", "B", ip="192.168.2.62")
    c = add_device(st, "aa:00:00:00:00:63", "C", ip="192.168.2.63")
    st.assets.assign([f"dev:{a}", f"dev:{b}", f"dev:{c}"], "asset:1")
    assert client.delete(f"/api/devices/{a}").status_code == 204
    assert f"dev:{a}" not in st.assets.links()
    assert client.post(f"/api/devices/{b}/merge", json={"into": c}).status_code == 200
    links = st.assets.links()
    assert f"dev:{b}" not in links and links[f"dev:{c}"] == "asset:1"


def test_the_hints_endpoint_lists_unclaimed_devices(client):
    st = client.app.state
    add_device(st, "aa:00:00:00:00:71", "Lonely NAS", ip="192.168.2.71")
    before = len(st.events.list(0, 10**14, kinds=[SECURITY]))
    labels = [d["label"] for d in client.get("/api/assets").json()["hints"]["unclaimed"]]
    assert "Lonely NAS" in labels
    assert len(st.events.list(0, 10**14, kinds=[SECURITY])) == before  # a read logs nothing
