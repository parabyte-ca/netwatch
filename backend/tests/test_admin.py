from __future__ import annotations

import gzip
import sqlite3

import pytest
from fastapi.testclient import TestClient

from netwatch.db import make_engine, run_migrations
from netwatch.main import create_app
from netwatch.services.restore import RestoreError, apply_pending_restore, validate_backup
from netwatch.services.storage import Store, backup_database

from .conftest import CSRF, login, make_target, test_settings


@pytest.fixture
def restarts(client):
    calls: list[int] = []
    client.app.state.restart = lambda *a, **k: calls.append(1)
    return calls


def field(body, name):
    return next(f for f in body["fields"] if f["name"] == name)


# ── settings ──────────────────────────────────────────────────────────────────


def test_settings_listing(client):
    body = client.get("/api/admin/settings").json()
    f = field(body, "down_after")
    assert f["value"] == 3 and f["default"] == 3 and f["type"] == "int"
    assert f["min"] == 1 and f["max"] == 100 and f["live"] is True and not f["overridden"]
    assert field(body, "log_level")["choices"] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert {f["group"] for f in body["fields"]} >= {"detection", "retention", "security"}


def test_live_setting_applies_and_persists(client):
    st = client.app.state
    r = client.put("/api/admin/settings", json={"changes": {"down_after": 5, "nic_poll_s": 4}})
    assert r.status_code == 200, r.text
    assert field(r.json(), "down_after")["overridden"] and r.json()["restart_pending"] == []
    assert st.settings.down_after == 5 and st.monitor.detector.config.down_after == 5
    assert st.nic.poll_s == 4
    assert st.store.kv_get("settings_overrides") == {"down_after": 5, "nic_poll_s": 4.0}
    log = client.get("/api/admin/security-log").json()
    assert any("Failures before down" in e["title"] for e in log)
    # back to the default
    r = client.put("/api/admin/settings", json={"changes": {"down_after": None}})
    assert st.settings.down_after == 3 and not field(r.json(), "down_after")["overridden"]
    assert st.store.kv_get("settings_overrides") == {"nic_poll_s": 4.0}


def test_restart_setting_is_flagged(client):
    r = client.put("/api/admin/settings", json={"changes": {"path_test_interval_min": 30}})
    assert r.json()["restart_pending"] == ["path_test_interval_min"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"down_after": 0}, "Failures before down"),
        ({"degraded_exit_pct": 9}, "lower than packet-loss start"),
        ({"raw_retention_days": 60, "rollup_retention_days": 30}, "Raw samples"),
        ({"discovery_subnets": "8.8.8.0/24"}, "not a private range"),
        ({"port": 1}, "not editable"),
    ],
)
def test_invalid_settings_rejected(client, changes, message):
    r = client.put("/api/admin/settings", json={"changes": changes})
    assert r.status_code == 422 and message in r.json()["detail"]
    assert client.app.state.store.kv_get("settings_overrides") in (None, {})


def test_metrics_token_is_masked(client):
    client.put("/api/admin/settings", json={"changes": {"metrics_token": "s3cret-token"}})
    body = client.get("/api/admin/settings").json()
    assert field(body, "metrics_token")["value"] == "••••••••"
    client.put("/api/admin/settings", json={"changes": {"metrics_token": "••••••••"}})
    assert client.app.state.settings.metrics_token == "s3cret-token"


def test_saved_overrides_apply_at_startup(tmp_path, prober):
    settings = test_settings(tmp_path)
    settings.data_dir.mkdir(parents=True)
    engine = make_engine(settings.db_path)
    run_migrations(engine)
    Store(engine).kv_set(
        "settings_overrides", {"down_after": 7, "login_max_failures": 9, "bogus": 1, "up_after": 0}
    )
    engine.dispose()
    with TestClient(create_app(settings, prober=prober)) as c:
        st = c.app.state
        assert settings.down_after == 7  # applied in place
        assert st.monitor.detector.config.down_after == 7
        assert st.login_throttle.max_failures == 9
        assert settings.up_after == 3  # invalid saved value ignored
        assert st.settings_base.down_after == 3


def test_restart_endpoint(client, restarts):
    assert client.post("/api/admin/restart").status_code == 202
    assert restarts == [1]


def test_admin_requires_login(anon_client):
    for path in ("/api/admin/settings", "/api/admin/sessions", "/api/admin/backups"):
        assert anon_client.get(path).status_code == 401


def test_metrics_disabled_is_404(client):
    assert client.get("/metrics").status_code == 404


# ── sessions ──────────────────────────────────────────────────────────────────


def test_sessions_list_and_revoke(client):
    auth = client.app.state.auth
    uid = auth.list_sessions()[0]["user_id"]
    auth.create_session(uid, 30, "192.168.2.50", "Firefox on phone")
    rows = client.get("/api/admin/sessions").json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    mine = next(r for r in rows if r["current"])
    other = next(r for r in rows if not r["current"])
    assert other["ip"] == "192.168.2.50"
    assert client.delete(f"/api/admin/sessions/{mine['id']}").status_code == 409
    assert client.delete(f"/api/admin/sessions/{other['id']}").status_code == 204
    assert client.delete(f"/api/admin/sessions/{other['id']}").status_code == 404
    auth.create_session(uid, 30, "1.2.3.4", "x")
    assert client.post("/api/admin/sessions/revoke-others").json() == {"revoked": 1}
    assert len(client.get("/api/admin/sessions").json()) == 1


def test_sign_in_events(anon_client):
    login(anon_client)
    r = anon_client.post("/api/auth/login", json={"username": "admin", "password": "wrong-pass"})
    assert r.status_code == 401
    log = anon_client.get("/api/admin/security-log").json()
    titles = [e["title"] for e in log]
    assert "Failed sign-in" in titles
    assert all(e["kind"] == "security" for e in log)


# ── backups and restore ───────────────────────────────────────────────────────


def test_backup_create_download_delete(client):
    r = client.post("/api/admin/backups")
    assert r.status_code == 201
    name = r.json()["name"]
    listing = client.get("/api/admin/backups").json()
    assert listing["backups"][0]["name"] == name and not listing["restore_pending"]
    dl = client.get(f"/api/admin/backups/{name}")
    assert dl.status_code == 200 and dl.content[:16] == b"SQLite format 3\x00"
    assert "attachment" in dl.headers["content-disposition"]
    assert client.get("/api/admin/backups/..%2Fnetwatch.db").status_code == 404
    assert client.get("/api/admin/backups/missing.db").status_code == 404
    assert client.delete(f"/api/admin/backups/{name}").status_code == 204
    assert client.get("/api/admin/backups").json()["backups"] == []


def test_restore_upload_is_staged_and_restarts(client, restarts):
    st = client.app.state
    make_target(st.store, name="Before backup")
    name = client.post("/api/admin/backups").json()["name"]
    data = client.get(f"/api/admin/backups/{name}").content
    r = client.post(
        "/api/admin/restore", content=gzip.compress(data),
        headers={**CSRF, "Content-Type": "application/octet-stream"},
    )  # fmt: skip
    assert r.status_code == 202, r.text
    assert r.json()["targets"] == 1 and restarts == [1]
    assert (st.settings.data_dir / "restore-pending.db").exists()
    assert client.get("/api/admin/backups").json()["restore_pending"]


def test_restore_rejects_garbage(client, restarts):
    r = client.post("/api/admin/restore", content=b"not a database" * 100,
                    headers={**CSRF, "Content-Type": "application/octet-stream"})  # fmt: skip
    assert r.status_code == 422 and "not a SQLite database" in r.json()["detail"]
    assert restarts == []
    assert not (client.app.state.settings.data_dir / "restore-pending.db").exists()


def test_restore_from_existing_backup(client, restarts):
    name = client.post("/api/admin/backups").json()["name"]
    assert client.post(f"/api/admin/backups/{name}/restore").status_code == 202
    assert restarts == [1]


def _db_with_target(path, name):
    e = make_engine(path)
    run_migrations(e)
    make_target(Store(e), name=name)
    e.dispose()


def test_pending_restore_is_applied_on_start(tmp_path, prober):
    settings = test_settings(tmp_path)
    settings.data_dir.mkdir(parents=True)
    _db_with_target(settings.db_path, "current")
    _db_with_target(tmp_path / "b.db", "restored")
    backup_database(tmp_path / "b.db", settings.data_dir / "restore-pending.db")
    with TestClient(create_app(settings, prober=prober)) as c:
        login(c)
        assert [t["name"] for t in c.get("/api/targets").json()] == ["restored"]
        assert not (settings.data_dir / "restore-pending.db").exists()
        safety = [b["name"] for b in c.get("/api/admin/backups").json()["backups"]]
        assert any(n.startswith("pre-restore-") for n in safety)
        log = c.get("/api/admin/security-log").json()
        assert any(e["title"] == "Database restored from a backup" for e in log)


def test_invalid_pending_restore_is_set_aside(tmp_path):
    db = tmp_path / "netwatch.db"
    _db_with_target(db, "current")
    (tmp_path / "restore-pending.db").write_bytes(b"junk")
    assert apply_pending_restore(db, tmp_path / "backups") is None
    assert (tmp_path / "restore-pending.rejected").exists()
    assert validate_backup(db)["targets"] == 1


def test_backup_from_newer_version_rejected(tmp_path):
    db = tmp_path / "x.db"
    _db_with_target(db, "t")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE alembic_version SET version_num = '9999'")
    conn.commit()
    conn.close()
    with pytest.raises(RestoreError, match="newer NetWatch"):
        validate_backup(db)
