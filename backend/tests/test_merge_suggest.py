from __future__ import annotations

import time

from netwatch.services.discovery.merge_suggest import merge_suggestions, pair_key

NOW = 1_800_000_000_000
HOUR = 3_600_000


def dev(i, label, *, online, hostname=None, ip=None, target=None, deco=False, seen=None, mdns=None):
    return {
        "id": i,
        "label": label,
        "hostname": hostname,
        "mdns_name": mdns,
        "last_ip": ip,
        "online": online,
        "last_seen_ms": seen if seen is not None else (NOW if online else NOW - 3 * HOUR),
        "target_id": target,
        "details": {"is_deco": True} if deco else {},
    }


def test_same_hostname_or_address_pairs_a_new_card_with_the_old_one():
    rows = [
        dev(1, "truenas-server", online=True, hostname="truenas-server", ip="192.168.2.30"),
        dev(2, "TrueNAS Server", online=False, hostname="truenas-server", ip="192.168.2.18"),
        dev(3, "moogbox", online=True, hostname="moogbox", ip="192.168.2.20"),
        dev(4, "moogbox old", online=False, hostname=None, ip="192.168.2.20"),
        dev(5, "Printer", online=False, hostname="printer", ip="192.168.2.50"),
    ]
    out = merge_suggestions(rows)
    pairs = {(s["new"]["id"], s["old"]["id"]): s["reasons"] for s in out}
    assert pairs == {
        (1, 2): ["same hostname (truenas-server)"],
        (3, 4): ["same address (192.168.2.20)"],
    }


def test_name_and_address_together_rank_first():
    rows = [
        dev(1, "box", online=True, hostname="boxy", ip="192.168.2.9"),
        dev(2, "box old", online=False, hostname="boxy", ip="192.168.2.9"),
        dev(3, "other", online=True, hostname="other-pc", ip="192.168.2.8"),
        dev(4, "other old", online=False, hostname="other-pc", ip="192.168.2.7"),
    ]
    out = merge_suggestions(rows)
    assert [s["new"]["id"] for s in out] == [1, 3] and len(out[0]["reasons"]) == 2


def test_never_pairs_two_online_devices_decos_generic_names_or_a_newer_old_row():
    rows = [
        dev(1, "a", online=True, hostname="server", ip="192.168.2.5"),
        dev(2, "b", online=True, hostname="server", ip="192.168.2.5"),  # both online: two cards
        dev(3, "deco", online=True, hostname="deco-main", ip="192.168.2.10", deco=True),
        dev(4, "deco old", online=False, hostname="deco-main", ip="192.168.2.10", deco=True),
        dev(5, "phone", online=True, hostname="android", ip="192.168.2.6"),  # generic name
        dev(6, "phone old", online=False, hostname="android", ip="192.168.2.77"),
        dev(7, "fresh", online=True, hostname="nas", seen=NOW - 10 * HOUR),
        dev(8, "later", online=False, hostname="nas", seen=NOW - HOUR),  # old row seen after new
    ]
    assert merge_suggestions(rows) == []


def test_dismissed_pairs_stay_hidden_and_monitoring_is_flagged():
    rows = [
        dev(1, "moogbox", online=True, hostname="moogbox", target=11),
        dev(2, "moogbox old", online=False, hostname="moogbox", target=12),
        dev(3, "nas", online=True, hostname="nas1"),
        dev(4, "nas old", online=False, hostname="nas1"),
    ]
    out = merge_suggestions(rows, {pair_key(4, 3)})
    assert [(s["new"]["id"], s["old"]["id"], s["both_monitored"]) for s in out] == [(1, 2, True)]
    assert out[0]["new"]["monitored"] and out[0]["old"]["monitored"]


def test_api_lists_dismisses_and_requires_the_same_origin(client):
    st = client.app.state
    now = int(time.time() * 1000)
    base = {"first_seen_ms": 0, "hostname": "truenas-server"}
    new = st.device_store.insert(
        {**base, "mac": "aa:00:00:00:00:01", "last_seen_ms": now, "last_ip": "192.168.2.30"}
    )
    old = st.device_store.insert(
        {**base, "mac": "aa:00:00:00:00:02", "last_seen_ms": now - 3 * HOUR,
         "last_ip": "192.168.2.18"}
    )  # fmt: skip
    st.discovery.inventory.forget()
    r = client.get("/api/devices/merge-suggestions")
    assert r.status_code == 200, r.text
    got = r.json()
    assert [(s["new"]["id"], s["old"]["id"]) for s in got] == [(new, old)]
    body = {"a": new, "b": old}
    assert client.post("/api/devices/merge-suggestions/dismiss", json=body,
                       headers={"X-Requested-With": ""}).status_code == 403  # fmt: skip
    assert client.post("/api/devices/merge-suggestions/dismiss",
                       json={"a": new, "b": 9999}).status_code == 404  # fmt: skip
    assert client.post("/api/devices/merge-suggestions/dismiss", json=body).status_code == 200
    assert client.get("/api/devices/merge-suggestions").json() == []
    # Accepting uses the ordinary merge, with the older device as the one that stays.
    r = client.post(f"/api/devices/{new}/merge", json={"into": old})
    assert r.status_code == 200 and r.json()["device"]["id"] == old


def test_the_suggestions_route_needs_a_login(anon_client):
    assert anon_client.get("/api/devices/merge-suggestions").status_code == 401
