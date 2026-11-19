from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, insert, inspect, select, text

from netwatch import db
from netwatch.db import device_ips, devices, events, make_engine, run_migrations
from netwatch.services.host_access import (
    AccessError,
    AccessStore,
    hosts_of,
    ssh_command,
    ssh_url,
    view,
    web_url,
)

from .conftest import CSRF

IP = "192.168.2.5"


def svc(port: int, banner: str | None = None, seen: int = 1_000) -> dict:
    return {"port": port, "banner": banner, "last_seen_ms": seen}


def add_device(st, mac="aa:bb:cc:dd:ee:55", ip=IP, **extra) -> int:
    return st.discovery.inventory.store.insert(
        {"mac": mac, "first_seen_ms": 0, "last_seen_ms": 5_000, "last_ip": ip, **extra}
    )


def test_link_forms():
    assert ssh_url(IP, 22, "admin") == "ssh://admin@192.168.2.5"
    assert ssh_url(IP, 2222, "root") == "ssh://root@192.168.2.5:2222"
    assert ssh_url("fd00::5", 22, "a") == "ssh://a@[fd00::5]"
    assert ssh_command(IP, 2222, "root") == "ssh root@192.168.2.5 -p 2222"
    assert web_url("https", IP, 443) == "https://192.168.2.5"
    assert web_url("http", IP, 80) == "http://192.168.2.5"
    assert web_url("http", IP, 8080) == "http://192.168.2.5:8080"
    assert web_url("https", IP, 8443) == "https://192.168.2.5:8443"


def test_view_picks_the_usual_web_port_first_and_lists_the_rest():
    v = view(None, [svc(22, "SSH-2.0-OpenSSH_9.2"), svc(80), svc(443), svc(8080)], IP)
    assert v["web"]["primary"]["url"] == "https://192.168.2.5"
    assert [p["url"] for p in v["web"]["others"]] == [
        "http://192.168.2.5",
        "http://192.168.2.5:8080",
    ]
    assert (
        v["ssh"]["available"] and v["ssh"]["detected"] and v["ssh"]["banner"].startswith("SSH-2.0")
    )
    assert v["ssh"]["ssh_url"] is None  # no user saved yet
    assert v["scanned_ms"] == 1_000
    only = view(None, [svc(5000)], IP)
    assert (
        only["web"]["primary"]["url"] == "http://192.168.2.5:5000" and only["web"]["others"] == []
    )
    assert view(None, [], IP)["web"] == {"primary": None, "others": []}


def test_view_ssh_rule_user_and_override():
    row = {"ssh_user": "alan", "ssh_port": 2222, "ssh_mode": "auto", "admin_url": None}
    v = view(row, [svc(22)], IP)
    assert v["ssh"]["ssh_url"] == "ssh://alan@192.168.2.5:2222"
    assert v["ssh"]["ssh_command"] == "ssh alan@192.168.2.5 -p 2222"
    assert view({**row, "ssh_mode": "auto"}, [svc(80)], IP)["ssh"]["available"] is False  # no 22
    assert view({**row, "ssh_mode": "on"}, [svc(80)], IP)["ssh"]["available"] is True
    assert view({**row, "ssh_mode": "off"}, [svc(22)], IP)["ssh"]["available"] is False
    assert view(row, [svc(22)], None)["ssh"]["available"] is False  # no address to point at
    over = view({"admin_url": "https://192.168.2.5:9443/ui"}, [svc(80)], IP)
    assert over["web"]["primary"]["url"] == "https://192.168.2.5:9443/ui"
    assert over["web"]["primary"]["label"] == "Saved address"
    assert [p["port"] for p in over["web"]["others"]] == [80]


@pytest.fixture
def access(store):
    return AccessStore(store.engine)


def _device(store, ip=IP) -> int:
    with store.engine.begin() as conn:
        return conn.execute(
            insert(devices).values(
                mac="aa:bb:cc:00:00:01", first_seen_ms=0, last_seen_ms=1, last_ip=ip, details="{}"
            )
        ).inserted_primary_key[0]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"ssh_user": "Root"}, "user name"),
        ({"ssh_user": "a b"}, "user name"),
        ({"ssh_user": "1abc"}, "user name"),
        ({"ssh_port": 0}, "port"),
        ({"ssh_port": 65536}, "port"),
        ({"ssh_mode": "sometimes"}, "auto, on or off"),
        ({"admin_url": "ftp://192.168.2.5/"}, "http or https"),
        ({"admin_url": "javascript:alert(1)"}, "http or https"),
        ({"admin_url": "http://evil.example/"}, "own address"),
        ({"admin_url": "http://192.168.2.99/"}, "own address"),
    ],
)
def test_store_validation(store, access, changes, message):
    did = _device(store)
    with pytest.raises(AccessError, match=message):
        access.save(device_id=did, hosts=hosts_of(IP, "nas.lan"), **changes)


def test_store_saves_and_updates_per_device(store, access):
    did = _device(store)
    assert access.get(device_id=did) is None
    hosts = hosts_of(IP, "NAS.lan")
    a = access.save(device_id=did, hosts=hosts, ssh_user="alan", ssh_port=2222)
    assert (a["ssh_user"], a["ssh_port"], a["ssh_mode"]) == ("alan", 2222, "auto")
    b = access.save(device_id=did, hosts=hosts, ssh_mode="on", admin_url="http://nas.lan:8080/x")
    assert (
        b["id"] == a["id"] and b["ssh_user"] == "alan" and b["admin_url"] == "http://nas.lan:8080/x"
    )
    c = access.save(device_id=did, hosts=hosts, ssh_user="", admin_url="")
    assert c["ssh_user"] is None and c["admin_url"] is None
    with pytest.raises(AccessError, match="one of"):
        access.save(hosts=hosts, ssh_user="x")
    with store.engine.begin() as conn:  # the row goes with its device
        conn.execute(delete(devices).where(devices.c.id == did))
    assert access.get(device_id=did) is None


def test_exactly_one_owner_is_enforced_by_the_table(store):
    did = _device(store)
    with (
        pytest.raises(Exception, match=r"ck_host_access_owner|CHECK"),
        store.engine.begin() as conn,
    ):
        conn.execute(
            text("INSERT INTO host_access (device_id, target_id) VALUES (:d, 1)"), {"d": did}
        )
    with (
        pytest.raises(Exception, match=r"ck_host_access_owner|CHECK"),
        store.engine.begin() as conn,
    ):
        conn.execute(text("INSERT INTO host_access (ssh_user) VALUES ('x')"))


def _alembic(engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(db.__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


def test_migration_moves_saved_servers_onto_devices(tmp_path):
    engine = make_engine(tmp_path / "m.db")
    cfg = _alembic(engine)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0013")
    with engine.begin() as conn:
        base = {"first_seen_ms": 0, "details": "{}"}
        nas = conn.execute(
            insert(devices).values(
                mac="aa:00:00:00:00:01", last_seen_ms=9, last_ip="192.168.2.5", **base
            )
        ).inserted_primary_key[0]
        ag = conn.execute(
            insert(devices).values(
                mac="aa:00:00:00:00:02", last_seen_ms=9, last_ip="192.168.2.99", **base
            )
        ).inserted_primary_key[0]
        sib = conn.execute(
            insert(devices).values(
                mac="da:00:00:00:00:03",
                last_seen_ms=9,
                last_ip="192.168.2.77",
                merged_into=ag,
                **base,
            )
        ).inserted_primary_key[0]
        conn.execute(
            insert(device_ips).values(
                device_id=ag, ip="192.168.2.53", first_seen_ms=0, last_seen_ms=1, source="arp"
            )
        )
        for i, (name, host, port, user) in enumerate(
            [
                ("TrueNAS", "192.168.2.5", 2222, "truenas_admin"),
                ("AdGuard", "192.168.2.53", 22, "root"),
                ("Via sibling", "192.168.2.77", 22, "pi"),
                ("Cloud box", "203.0.113.9", 22, "ubuntu"),
            ]
        ):
            conn.execute(
                text(
                    "INSERT INTO servers (name, host, port, username, sort, created_ms) "
                    "VALUES (:n, :h, :p, :u, :s, 1)"
                ),
                {"n": name, "h": host, "p": port, "u": user, "s": i},
            )
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert "servers" not in insp.get_table_names() and "host_access" in insp.get_table_names()
    with engine.connect() as conn:
        rows = {r.device_id: r for r in conn.execute(text("SELECT * FROM host_access")).all()}
        log = [
            r.title
            for r in conn.execute(select(events.c.title).where(events.c.kind == "security")).all()
        ]
    assert (
        rows[nas].ssh_user == "truenas_admin"
        and rows[nas].ssh_port == 2222
        and rows[nas].ssh_mode == "on"
    )
    assert rows[ag].ssh_user in (
        "root",
        "pi",
    )  # the AdGuard device, by its older address or via its sibling
    assert sib not in rows  # moved onto the primary, never the sibling row
    assert len(rows) == 2
    assert any("Cloud box" in t and "was not moved" in t for t in log)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0013")
    insp = inspect(engine)
    assert "servers" in insp.get_table_names() and "host_access" not in insp.get_table_names()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    assert "host_access" in inspect(engine).get_table_names()
    run_migrations(engine)  # idempotent at head


def test_api_get_put_and_log(client):
    st = client.app.state
    did = add_device(st, name="NAS")
    st.exposure_store.record({did: {22: "SSH-2.0-OpenSSH_9.2", 443: "", 80: ""}}, {did}, 2_000)
    assert client.get("/api/devices/9999/access").status_code == 404
    v = client.get(f"/api/devices/{did}/access").json()
    assert v["ssh"]["available"] and v["ssh"]["ssh_url"] is None
    assert v["web"]["primary"]["url"] == "https://192.168.2.5" and v["scanned_ms"] == 2_000
    no_csrf = {"X-Requested-With": ""}
    assert (
        client.put(
            f"/api/devices/{did}/access", json={"ssh_user": "a"}, headers=no_csrf
        ).status_code
        == 403
    )
    r = client.put(
        f"/api/devices/{did}/access", json={"ssh_user": "alan", "ssh_port": 2222}, headers=CSRF
    )
    assert r.status_code == 200, r.text
    assert r.json()["ssh"]["ssh_url"] == "ssh://alan@192.168.2.5:2222"
    for bad in (
        {"ssh_user": "Bad User"},
        {"ssh_port": 0},
        {"ssh_mode": "x"},
        {"admin_url": "http://evil.example/"},
        {"nope": 1},
    ):
        assert (
            client.put(f"/api/devices/{did}/access", json=bad, headers=CSRF).status_code == 422
        ), bad
    r = client.put(
        f"/api/devices/{did}/access", json={"admin_url": "http://192.168.2.5:8080/ui"}, headers=CSRF
    )
    assert r.json()["web"]["primary"]["url"] == "http://192.168.2.5:8080/ui"
    assert (
        client.put(f"/api/devices/{did}/access", json={"ssh_mode": "off"}, headers=CSRF).json()[
            "ssh"
        ]["available"]
        is False
    )
    log = [e["title"] for e in client.get("/api/admin/security-log").json()]
    assert any(x.startswith("Access: changed ssh_port, ssh_user for") for x in log)
    assert "alan" not in " ".join(log)  # field names only
    # Saving the same values again, as the page does, is not a change and is not logged.
    before = len(log)
    same = {"ssh_user": "alan", "ssh_port": 2222, "ssh_mode": "off",
            "admin_url": "http://192.168.2.5:8080/ui"}  # fmt: skip
    assert client.put(f"/api/devices/{did}/access", json=same, headers=CSRF).status_code == 200
    log = [e["title"] for e in client.get("/api/admin/security-log").json()]
    assert len(log) == before


def test_api_merged_device_uses_its_primary(client):
    st = client.app.state
    primary = add_device(st, mac="aa:bb:cc:dd:ee:56", ip="192.168.2.20")
    sibling = add_device(st, mac="da:bb:cc:dd:ee:57", ip="192.168.2.21", merged_into=primary)
    client.put(f"/api/devices/{sibling}/access", json={"ssh_user": "pi"}, headers=CSRF)
    assert client.get(f"/api/devices/{primary}/access").json()["ssh"]["user"] == "pi"
    assert client.get(f"/api/devices/{primary}/access").json()["ip"] == "192.168.2.20"


def test_api_check_probes_only_this_device_and_keeps_other_findings(client):
    st = client.app.state
    did = add_device(st)
    st.exposure_store.record({did: {445: "", 80: ""}}, {did}, 1_000)
    seen: list[tuple[str, int]] = []

    async def probe(ip, port):
        seen.append((ip, port))
        return (port in (22, 443), "SSH-2.0-OpenSSH_9.2" if port == 22 else "")

    st.exposure.probe = probe
    v = client.post(f"/api/devices/{did}/access/check", headers=CSRF).json()
    assert {ip for ip, _ in seen} == {IP}
    assert sorted(p for _, p in seen) == [22, 80, 443, 5000, 8080, 8123, 8443, 32400]
    assert v["ssh"]["detected"] and v["web"]["primary"]["url"] == "https://192.168.2.5"
    assert v["web"]["others"] == []  # port 80 closed now, so it was dropped
    ports = {s["port"] for s in st.exposure_store.services(did)}
    assert ports == {22, 443, 445}  # 445 was not part of the check, so it stays
    assert client.post(f"/api/devices/{did}/access/check", headers=CSRF).status_code == 429
    no_csrf = {"X-Requested-With": ""}
    assert client.post(f"/api/devices/{did}/access/check", headers=no_csrf).status_code == 403


def test_api_check_refuses_a_device_without_a_lan_address(client):
    st = client.app.state
    none = add_device(st, mac="aa:bb:cc:dd:ee:58", ip=None)
    assert client.post(f"/api/devices/{none}/access/check", headers=CSRF).status_code == 422
    public = add_device(st, mac="aa:bb:cc:dd:ee:59", ip="8.8.8.8")
    r = client.post(f"/api/devices/{public}/access/check", headers=CSRF)
    assert r.status_code == 422 and "LAN" in r.text


def test_api_needs_sign_in(anon_client):
    assert anon_client.get("/api/devices/1/access").status_code == 401
    assert anon_client.put("/api/devices/1/access", json={}, headers=CSRF).status_code == 401
    assert anon_client.post("/api/devices/1/access/check", headers=CSRF).status_code == 401
