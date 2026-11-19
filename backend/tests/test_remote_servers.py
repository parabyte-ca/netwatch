from __future__ import annotations

import time

import pytest

from netwatch.services.diagnosis import LAYER_LABEL, REMOTE, Diagnoser, target_layer
from netwatch.services.events import EventLog
from netwatch.services.netinfo import DefaultRoute
from netwatch.services.pathtest import PathStore

from .conftest import CSRF, make_target
from .test_diagnosis import GW, NOW, T0, net, outage, samples, scenario  # noqa: F401

NOW_MS = int(time.time() * 1000)


@pytest.fixture
def diag(store):
    return Diagnoser(
        store,
        EventLog(store.engine),
        PathStore(store.engine),
        gateway=lambda: DefaultRoute("br0", GW),
    )


@pytest.fixture
def vps(store):
    return make_target(store, name="Cloud VPS", host="203.0.113.7", group="remote")


def test_remote_group_has_its_own_layer_whatever_the_check_or_address():
    for kind, host in (
        ("icmp", "203.0.113.7"),
        ("tcp", "10.9.0.4"),
        ("dns", "9.9.9.9"),
        ("http", "x.example"),
    ):
        t = {"group": "remote", "kind": kind, "host": host}
        assert target_layer(t, GW) == REMOTE
    assert target_layer({"group": "public", "kind": "icmp", "host": "1.1.1.1"}, GW) == "internet"
    assert LAYER_LABEL[REMOTE] == "Remote servers"


def test_a_remote_server_failing_alone_is_no_incident(store, diag, net, vps):  # noqa: F811
    for t in (*net.values(), vps):
        samples(store, t, T0 - 300_000, T0 + 300_000, ok=True)
    samples(store, vps, T0, T0 + 120_000, ok=False)
    outage(diag, vps, T0, T0 + 120_000)
    assert diag.incidents(T0 - 600_000, NOW, NOW) == []


def test_a_remote_server_never_changes_the_verdict(store, diag, net, vps):  # noqa: F811
    net = {**net, "vps": vps}
    (inc,) = scenario(store, diag, net, {"cf", "goog", "dns_cf", "vps"})
    assert inc.causes[0].id == "isp" and inc.causes[0].confidence >= 85
    assert vps["id"] not in inc.failing and vps["id"] not in inc.healthy


def test_a_healthy_remote_server_is_not_evidence_for_the_internet(store, diag, net, vps):  # noqa: F811
    net = {**net, "vps": vps}
    (inc,) = scenario(store, diag, net, {"cf", "goog", "dns_cf"})
    assert vps["id"] not in inc.healthy  # the VPS answered, but that says nothing about Bell
    assert inc.causes[0].id == "isp"


def test_api_accepts_the_group_and_keeps_it_out_of_diagnosis_and_the_bell_report(client):
    st = client.app.state
    hub = make_target(st.store, name="Gateway", host=GW, group="gateway")
    cf = make_target(st.store, name="Cloudflare", host="1.1.1.1", group="public")
    r = client.post(
        "/api/targets",
        json={"name": "Cloud VPS", "kind": "icmp", "host": "203.0.113.7", "interval_s": 5,
              "enabled": True, "group": "remote"},
        headers=CSRF,
    )  # fmt: skip
    assert r.status_code == 201, r.text
    vps = r.json()
    assert vps["group"] == "remote"
    for t in (hub, cf):
        samples(st.store, t, T0 - 120_000, T0 + 240_000, ok=True)
    samples(st.store, vps, T0 - 120_000, T0 + 240_000, ok=False)
    st.events.open("outage", "critical", T0, "Cloud VPS unreachable", target_id=vps["id"],
                   end_ms=T0 + 120_000)  # fmt: skip
    body = client.get("/api/diagnosis").json()
    assert body["incidents"] == []
    assert REMOTE not in {n["layer"] for n in body["topology"]}
    assert all(m["name"] != "Cloud VPS" for n in body["topology"] for m in n["members"])
    rep = client.get("/api/report").json()
    assert all(t["name"] != "Cloud VPS" for t in rep["targets"])
    assert "Cloud VPS" not in client.get("/api/report", params={"format": "md"}).text


def test_map_shows_the_remote_server_beyond_the_internet_node(client):
    st = client.app.state
    t = make_target(st.store, name="Cloud VPS", host="203.0.113.7", group="remote")
    off = make_target(
        st.store, name="Retired box", host="203.0.113.8", group="remote", enabled=False
    )
    samples(st.store, t, NOW - 120_000, NOW, ok=True)
    g = client.get("/api/map").json()
    nodes = {n["id"]: n for n in g["nodes"]}
    n = nodes[f"remote:{t['id']}"]
    assert n["kind"] == "remote" and n["label"] == "Cloud VPS"
    assert n["info"] == {"host": "203.0.113.7", "check": "icmp", "target_id": t["id"]}
    assert n["members"][0]["target_id"] == t["id"]
    assert f"remote:{off['id']}" not in nodes  # a disabled target is not drawn
    edges = {e["id"]: e for e in g["edges"]}
    e = edges[f"internet>remote:{t['id']}"]
    assert e["medium"] == "logical" and e["source"] == "internet"
    # The core chain is unchanged: the remote node is not a member of the Internet node.
    assert all(m["target_id"] != t["id"] for m in nodes["internet"]["members"])


def test_map_replay_draws_the_remote_server_with_its_state_back_then(client):
    st = client.app.state
    t = make_target(st.store, name="Cloud VPS", host="203.0.113.7", group="remote")
    samples(st.store, t, T0 - 120_000, T0 + 240_000, ok=False)
    st.events.open("outage", "critical", T0, "Cloud VPS unreachable", target_id=t["id"],
                   end_ms=T0 + 120_000)  # fmt: skip
    g = client.get("/api/map", params={"at": T0 + 30_000}).json()
    n = next(x for x in g["nodes"] if x["id"] == f"remote:{t['id']}")
    assert n["state"] == "down"
    assert g["at_ms"] is not None
