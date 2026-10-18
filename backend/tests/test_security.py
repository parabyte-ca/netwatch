"""Regression tests for the v0.7.1 security review fixes."""

from __future__ import annotations

import asyncio
import gzip
import json
import sqlite3
import time

import httpx
import pytest

from netwatch.auth import LoginThrottle
from netwatch.db import make_engine, run_migrations
from netwatch.main import host_allowed
from netwatch.services import restore as restore_mod
from netwatch.services.deco.client import DecoClient, DecoKeyChanged, scrub
from netwatch.services.deco.service import MAX_LOGIN_FAILURES, PASSWORD_KEY, PIN_KEY
from netwatch.services.events import SECURITY
from netwatch.services.restore import RestoreError, apply_pending_restore, validate_backup
from netwatch.services.updates import UpgradeFolder

from .conftest import ADMIN
from .test_deco import PASSWORD, FakeDeco, client_for

# ── web layer ─────────────────────────────────────────────────────────────────


def test_host_allowlist():
    assert host_allowed("192.168.2.15:8090", set())
    assert host_allowed("[fd00::1]:8090", set())
    assert host_allowed("localhost:8090", set())
    assert not host_allowed("evil.example:8090", set())  # DNS rebinding
    assert not host_allowed(None, set())
    allowed = {"192.168.2.15", "netwatch.lan"}
    assert host_allowed("NetWatch.lan", allowed)
    assert host_allowed("127.0.0.1:8090", allowed)  # the container health check
    assert not host_allowed("192.168.2.99:8090", allowed)


def test_unknown_host_is_refused(client):
    r = client.get("/api/auth/status", headers={"Host": "rebind.example:8090"})
    assert r.status_code == 421
    assert r.json()["host"] == "rebind.example" and "NETWATCH_ALLOWED_HOSTS" in r.json()["detail"]


def test_large_bodies_are_refused(client):
    r = client.post(
        "/api/auth/login", content=b"x" * (300 * 1024), headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_no_api_schema_and_metrics_always_gated(anon_client):
    assert anon_client.get("/openapi.json").status_code == 404
    assert anon_client.get("/metrics").status_code == 401  # even while metrics are off


def test_health_hides_error_text(client):
    client.app.state.monitor.last_flush_error = "disk I/O error at /data/netwatch.db"
    body = client.get("/health").json()
    assert "writer_error" not in body and body["checks"]["writer"] is False


# ── sign-in throttle ──────────────────────────────────────────────────────────


def test_parallel_attempts_are_counted_up_front():
    t = LoginThrottle(max_failures=3, window_s=60, lockout_s=120)
    assert [t.begin("ip", now=0.0) for _ in range(5)] == [0, 0, 0, 1, 1]
    for _ in range(3):
        t.end("ip")
        t.failure("ip", now=1.0)
    assert t.begin("ip", now=2.0) == 119
    assert t.log_refusal("ip", now=2.0) and not t.log_refusal("ip", now=3.0)


def test_throttle_memory_is_bounded():
    t = LoginThrottle(max_failures=3, window_s=60, lockout_s=120)
    t.MAX_KEYS = 50
    for i in range(500):
        t.failure(f"2001:db8:{i}::/64", now=float(i))
    assert len(t._fails) <= 50


def test_ipv6_clients_are_throttled_per_64(client):
    from netwatch.api.auth import _client

    class R:
        def __init__(self, host):
            self.client = type("C", (), {"host": host})()

    assert _client(R("2001:db8:1:2::5")) == _client(R("2001:db8:1:2:aaaa::9"))
    assert _client(R("192.168.2.20")) == "192.168.2.20"


def test_password_change_is_throttled(client):
    bad = {"current_password": "not-it-at-all", "new_password": "another-long-pass"}
    codes = [client.post("/api/auth/password", json=bad).status_code for _ in range(6)]
    assert codes[:5] == [403] * 5 and codes[5] == 429


def test_lockout_is_logged_once(client):
    client.post("/api/auth/logout")
    bad = {**ADMIN, "password": "wrong-password"}
    for _ in range(12):
        client.post("/api/auth/login", json=bad)
    events = client.app.state.events.list(0, int(time.time() * 1000) + 1, kinds=[SECURITY])
    assert sum(e["title"] == "Sign-in refused: client locked out" for e in events) == 1


# ── restore ───────────────────────────────────────────────────────────────────


def _db(tmp_path, name="b.db"):
    path = tmp_path / name
    engine = make_engine(path)
    run_migrations(engine)
    engine.dispose()
    return path


def test_backup_with_trigger_is_rejected(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TRIGGER evil AFTER INSERT ON targets BEGIN UPDATE users SET password_hash='x'; END"
    )
    conn.commit()
    conn.close()
    with pytest.raises(RestoreError, match="trigger evil"):
        validate_backup(path)


def test_gzip_bomb_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(restore_mod, "MAX_RESTORE_BYTES", 1024 * 1024)
    src = tmp_path / "up.tmp"
    with gzip.open(src, "wb") as f:
        f.write(b"\0" * (8 * 1024 * 1024))
    dest = tmp_path / "out.db"
    with pytest.raises(RestoreError, match="size limit"):
        restore_mod.unpack_upload(src, dest)
    assert not dest.exists() and not src.exists()


def test_restore_drops_sessions(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    live = _db(data, "netwatch.db")
    pending = _db(data, "restore-pending.db")
    conn = sqlite3.connect(pending)
    conn.execute(
        "INSERT INTO users (username, password_hash, created_at, updated_at) "
        "VALUES ('admin', 'h', 0, 0)"
    )
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_ms, expires_ms, last_seen_ms) "
        "VALUES ('t', 1, 0, 9999999999999, 0)"
    )
    conn.commit()
    conn.close()
    apply_pending_restore(live, data / "backups")
    conn = sqlite3.connect(live)
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    conn.close()


def test_concurrent_restores_use_separate_files(client):
    raw = _db(client.app.state.settings.data_dir.parent, "x.db").read_bytes()
    client.app.state.restart = lambda *a, **k: None
    r = client.post(
        "/api/admin/restore", content=raw, headers={"Content-Type": "application/octet-stream"}
    )
    assert r.status_code == 202, r.text
    leftovers = list(client.app.state.settings.data_dir.glob(".restore-*"))
    assert leftovers == []


# ── upgrade folder ────────────────────────────────────────────────────────────


def test_status_comes_from_the_read_only_folder(tmp_path):
    inbox, out = tmp_path / "upgrade", tmp_path / "upgrade-status"
    inbox.mkdir()
    out.mkdir()
    (inbox / "status.json").write_text(json.dumps({"state": "succeeded", "id": "planted"}))
    folder = UpgradeFolder(inbox, out)
    assert folder.status()["id"] == "planted"  # old agent: reports still in the inbox
    (out / "agent.json").write_text(json.dumps({"last_seen": "x", "can_rollback": True}))
    (out / "status.json").write_text(json.dumps({"state": "failed", "id": "real"}))
    assert folder.status()["id"] == "real"
    req = folder.write_request("rollback", None, "manual", "admin")
    assert (inbox / "request.json").exists() and folder.request()["id"] == req["id"]
    assert not list(inbox.glob(".request.*"))


# ── Deco ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake():
    return FakeDeco()


def test_scrub_removes_the_session_token():
    msg = "Server error '500' for url 'http://192.168.2.10/cgi-bin/luci/;stok=abc123/admin/x'"
    assert "abc123" not in scrub(msg) and ";stok=…/admin" in scrub(msg)


async def test_changed_sign_in_key_is_refused(fake):
    c = client_for(fake)
    await c.list_decos()
    pin = c.key_seen
    await c.aclose()
    impostor = FakeDeco()  # same password, different keys
    c = DecoClient("192.168.2.10", PASSWORD, transport=httpx.MockTransport(impostor), key_pin=pin)
    with pytest.raises(DecoKeyChanged):
        await c.list_decos()
    assert impostor.logins == 0  # the password was never sent
    await c.aclose()


async def test_redirect_to_another_host_is_not_followed(fake):
    def redirect(req):
        return httpx.Response(302, headers={"location": "https://evil.example/login"})

    c = DecoClient("192.168.2.10", PASSWORD, transport=httpx.MockTransport(redirect))
    with pytest.raises(Exception, match="redirected"):
        await c.list_decos()
    await c.aclose()


async def test_same_host_https_upgrade_is_followed(fake):
    def handler(req):
        if req.url.scheme == "http":
            return httpx.Response(
                302, headers={"location": f"https://{req.url.host}{req.url.raw_path.decode()}"}
            )
        return fake(req)

    c = DecoClient("192.168.2.10", PASSWORD, transport=httpx.MockTransport(handler))
    assert len(await c.list_decos()) == 2 and c.host.startswith("https://")
    await c.aclose()


@pytest.fixture
def deco(client, fake):
    st = client.app.state
    st.deco.client_factory = lambda host, pw: client_for(fake, pw)
    return st.deco


def test_stored_password_only_goes_to_the_saved_deco(client, deco):
    deco.save("192.168.2.10", False, PASSWORD)
    r = client.post("/api/admin/deco/test", json={"host": "192.168.2.66"})
    assert r.status_code == 422 and "password" in r.text
    assert client.post("/api/admin/deco/test", json={"host": "192.168.2.10"}).json()["ok"]


def test_public_names_and_addresses_are_refused(client, deco, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("8.8.8.8", 80))])
    r = client.put("/api/admin/deco", json={"host": "deco.example.com", "enabled": False})
    assert r.status_code == 422 and "LAN" in r.text
    r = client.put("/api/admin/deco", json={"host": "2002:c0a8:20a::1", "enabled": False})
    assert r.status_code == 422


def test_changing_host_forgets_the_password(client, deco):
    deco.save("192.168.2.10", False, PASSWORD)
    deco.store.kv_set(PIN_KEY, {"host": "192.168.2.10", "fp": "x"})
    client.put("/api/admin/deco", json={"host": "192.168.2.11", "enabled": False})
    assert deco.store.kv_get(PASSWORD_KEY) is None and deco.store.kv_get(PIN_KEY) is None


def test_key_pinning_end_to_end(client, deco, fake):
    st = client.app.state
    deco.save("192.168.2.10", True, PASSWORD)
    assert client.portal.call(deco.poll)
    first = deco.pin("192.168.2.10")
    assert first
    # The Deco is "replaced": polling stops and a security event is recorded.
    impostor = FakeDeco()
    deco.client_factory = lambda host, pw: client_for(impostor, pw)
    deco._client_key = None
    assert not client.portal.call(deco.poll)
    assert deco.paused and impostor.logins == 0
    events = st.events.list(0, int(time.time() * 1000) + 1, kinds=[SECURITY])
    assert any("sign-in key changed" in e["title"] for e in events)
    # The stored password does not re-trust; the owner typing it does.
    assert not client.post("/api/admin/deco/test", json={"host": "192.168.2.10"}).json()["ok"]
    r = client.post("/api/admin/deco/test", json={"host": "192.168.2.10", "password": PASSWORD})
    assert r.json()["ok"] and deco.pin("192.168.2.10") != first and deco.paused is None


def test_polling_stops_after_repeated_sign_in_failures(client, deco, fake):
    deco.save("192.168.2.10", True, "wrong")
    for _ in range(MAX_LOGIN_FAILURES):
        assert not client.portal.call(deco.poll)
    assert deco.paused and "failed sign-ins" in deco.paused
    deco.save("192.168.2.10", True, PASSWORD)
    assert deco.paused is None


def test_untrusted_deco_strings_are_cleaned():
    from netwatch.services.deco.service import normalize_client, normalize_deco

    d = normalize_deco({"mac": "AA-BB-CC-00-00-03", "custom_nickname": "Office‮" + "x" * 99})
    assert "‮" not in d["name"] and len(d["name"]) <= 63
    assert normalize_client({"mac": "aa:bb:cc:dd:ee", "name": "x"}, "m")["mac"] == ""
    assert asyncio.iscoroutinefunction(DecoClient.list_decos)


def test_chunked_bodies_are_accepted_but_capped(client):
    """A reverse proxy or HTTP/2 hop can forward a request chunked, without Content-Length."""

    def chunks(data: bytes):
        yield from (data[i : i + 1000] for i in range(0, len(data), 1000))

    body = b'{"username": "admin", "password": "wrong-password-here"}'
    r = client.post(
        "/api/auth/login",
        content=chunks(body),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401  # reached the endpoint (not 411)
    r = client.post(
        "/api/auth/login",
        content=chunks(b"x" * (300 * 1024)),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
