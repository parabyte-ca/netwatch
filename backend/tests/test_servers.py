from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from netwatch import db
from netwatch.db import make_engine, run_migrations
from netwatch.services.servers import MAX_SERVERS, ServerError, ServerStore, ssh_command, ssh_url

from .conftest import CSRF


@pytest.fixture
def servers(store):
    return ServerStore(store.engine)


def test_ssh_link_and_command_forms():
    assert ssh_url("192.168.2.5", 22, "admin") == "ssh://admin@192.168.2.5"
    assert ssh_url("192.168.2.5", 2222, "root") == "ssh://root@192.168.2.5:2222"
    assert ssh_url("fd00::5", 22, "alan") == "ssh://alan@[fd00::5]"
    assert ssh_url("fd00::5", 2200, "alan") == "ssh://alan@[fd00::5]:2200"
    assert ssh_command("truenas.lan", 22, "admin") == "ssh admin@truenas.lan"
    assert ssh_command("truenas.lan", 2222, "admin") == "ssh admin@truenas.lan -p 2222"


def test_create_list_update_delete(servers):
    a = servers.create("TrueNAS", "192.168.2.5", 22, "truenas_admin", "main box")
    b = servers.create("AdGuard Home", "192.168.2.53", 22, "root")
    assert [s["name"] for s in servers.list()] == ["TrueNAS", "AdGuard Home"]  # in the order added
    assert a["ssh_url"] == "ssh://truenas_admin@192.168.2.5" and a["notes"] == "main box"
    c = servers.update(b["id"], port=2222, notes="")
    assert c["port"] == 2222 and c["notes"] is None and c["ssh_command"].endswith("-p 2222")
    assert servers.delete(a["id"])["name"] == "TrueNAS"
    assert [s["name"] for s in servers.list()] == ["AdGuard Home"]
    with pytest.raises(ServerError, match="no such server"):
        servers.delete(a["id"])
    with pytest.raises(ServerError, match="no such server"):
        servers.update(9999, port=22)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "  "}, "name"),
        ({"name": "x" * 41}, "name"),
        ({"username": "Root"}, "user name"),
        ({"username": "ro ot"}, "user name"),
        ({"username": "1root"}, "user name"),
        ({"username": ""}, "user name"),
        ({"port": 0}, "port"),
        ({"port": 70000}, "port"),
        ({"host": "bad host"}, "address"),
        ({"host": ""}, "address"),
        ({"notes": "n" * 201}, "notes"),
    ],
)
def test_validation(servers, kwargs, message):
    ok = {"name": "Box", "host": "192.168.2.9", "port": 22, "username": "admin"}
    with pytest.raises(ServerError, match=message):
        servers.create(**{**ok, **kwargs})


def test_names_are_unique_ignoring_case_and_the_list_is_capped(servers):
    servers.create("TrueNAS", "192.168.2.5", 22, "admin")
    with pytest.raises(ServerError, match="already exists"):
        servers.create(" truenas ", "192.168.2.6", 22, "admin")
    other = servers.create("Other", "192.168.2.6", 22, "admin")
    with pytest.raises(ServerError, match="already exists"):
        servers.update(other["id"], name="TRUENAS")
    servers.update(other["id"], name="OTHER")  # its own name in another case is fine
    for i in range(MAX_SERVERS - 2):
        servers.create(f"S{i}", f"192.168.3.{i + 1}", 22, "admin")
    with pytest.raises(ServerError, match="at most"):
        servers.create("One too many", "192.168.4.1", 22, "admin")


def test_migration_adds_and_removes_the_table(tmp_path):
    engine = make_engine(tmp_path / "m.db")
    run_migrations(engine)
    assert "servers" in inspect(engine).get_table_names()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(db.__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0012")
    assert "servers" not in inspect(engine).get_table_names()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    assert "servers" in inspect(engine).get_table_names()


def test_api_crud_guards_and_log(client):
    body = {"name": "TrueNAS", "host": "192.168.2.5", "port": 2222, "username": "truenas_admin"}
    no_csrf = {"X-Requested-With": ""}
    assert client.post("/api/admin/servers", json=body, headers=no_csrf).status_code == 403
    r = client.post("/api/admin/servers", json=body, headers=CSRF)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["ssh_url"] == "ssh://truenas_admin@192.168.2.5:2222"
    assert s["ssh_command"] == "ssh truenas_admin@192.168.2.5 -p 2222"
    assert client.get("/api/admin/servers").json()["servers"][0]["name"] == "TrueNAS"
    r = client.put(
        f"/api/admin/servers/{s['id']}", json={"port": 22, "notes": "main"}, headers=CSRF
    )
    assert r.json()["port"] == 22 and r.json()["notes"] == "main"
    assert client.put("/api/admin/servers/999", json={"port": 22}, headers=CSRF).status_code == 404
    assert client.post("/api/admin/servers", json=body, headers=CSRF).status_code == 422  # name
    bad = {**body, "name": "Bad", "username": "Root User"}
    assert client.post("/api/admin/servers", json=bad, headers=CSRF).status_code == 422
    assert (
        client.post(
            "/api/admin/servers", json={**body, "name": "x", "extra": 1}, headers=CSRF
        ).status_code
        == 422
    )
    r = client.delete(f"/api/admin/servers/{s['id']}", headers=CSRF)
    assert r.json() == {"removed": "TrueNAS"}
    assert client.get("/api/admin/servers").json()["servers"] == []
    log = [e["title"] for e in client.get("/api/admin/security-log").json()]
    assert "Servers: added TrueNAS (truenas_admin@192.168.2.5:2222)" in log
    assert any(x.startswith("Servers: changed TrueNAS") for x in log)
    assert "Servers: removed TrueNAS" in log


def test_api_only_lan_addresses(client):
    r = client.post(
        "/api/admin/servers",
        json={"name": "Public", "host": "8.8.8.8", "port": 22, "username": "root"},
        headers=CSRF,
    )
    assert r.status_code == 422 and "LAN" in r.text
    r = client.post(
        "/api/admin/servers",
        json={"name": "Odd", "host": "http://x/", "port": 22, "username": "root"},
        headers=CSRF,
    )
    assert r.status_code == 422
    r = client.post(
        "/api/admin/servers",
        json={"name": "Moved", "host": "192.168.2.7", "port": 22, "username": "root"},
        headers=CSRF,
    )
    sid = r.json()["id"]
    r = client.put(f"/api/admin/servers/{sid}", json={"host": "1.1.1.1"}, headers=CSRF)
    assert r.status_code == 422 and "LAN" in r.text


def test_api_suggestions_from_integrations_and_devices(client):
    st = client.app.state
    st.adguard.save("192.168.2.53", 80, False, "netwatch", True, "pw")
    st.deco.save("192.168.2.10", False, "pw")
    st.hub.save("192.168.2.1", False, "pw")
    st.discovery.inventory.store.insert(
        {"mac": "aa:bb:cc:dd:ee:77", "first_seen_ms": 0, "last_seen_ms": 10**13, "name": "NAS",
         "last_ip": "192.168.2.5"}
    )  # fmt: skip
    client.post(
        "/api/admin/servers",
        json={"name": "Hub", "host": "192.168.2.1", "port": 22, "username": "root"},
        headers=CSRF,
    )
    got = client.get("/api/admin/servers/suggestions").json()["suggestions"]
    hosts = {s["host"]: s["label"] for s in got}
    assert hosts["192.168.2.53"] == "AdGuard Home" and hosts["192.168.2.10"] == "Main Deco"
    assert "192.168.2.1" not in hosts  # already saved
    assert hosts.get("192.168.2.5") == "NAS"


def test_api_needs_sign_in(anon_client):
    assert anon_client.get("/api/admin/servers").status_code == 401
    assert anon_client.get("/api/admin/servers/suggestions").status_code == 401
    r = anon_client.post(
        "/api/admin/servers",
        json={"name": "x", "host": "192.168.2.2", "port": 22, "username": "a"},
        headers=CSRF,
    )
    assert r.status_code == 401
