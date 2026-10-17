import time

from netwatch import __version__
from netwatch.main import create_app
from netwatch.services.netinfo import DefaultRoute

from .conftest import FakeProber, login, test_settings

GW = {"name": "Gateway", "kind": "icmp", "host": "192.168.2.1", "interval_s": 1, "group": "gateway"}


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while not cond():
        assert time.time() < end, "condition not met in time"
        time.sleep(0.05)


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["version"] == __version__
    assert body["checks"] == {"database": True, "scheduler": True, "writer": True}


def test_health_reports_writer_failure(client):
    client.app.state.monitor.last_flush_error = "OperationalError: disk I/O error"
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["checks"]["writer"] is False


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_target_crud_and_validation(client):
    assert client.get("/api/targets").json() == []
    r = client.post("/api/targets", json=GW)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["timeout_s"] == 1.0 and t["auto"] is False

    assert client.post("/api/targets", json={**GW, "host": "-rf"}).status_code == 422
    assert client.post("/api/targets", json={**GW, "interval_s": 0.1}).status_code == 422

    upd = client.put(f"/api/targets/{t['id']}", json={**GW, "name": "Hub", "interval_s": 2})
    assert upd.status_code == 200 and upd.json()["name"] == "Hub"
    assert client.put("/api/targets/999", json=GW).status_code == 404

    assert client.delete(f"/api/targets/{t['id']}").status_code == 204
    assert client.delete(f"/api/targets/{t['id']}").status_code == 404
    assert client.get(f"/api/targets/{t['id']}").status_code == 404


def test_target_limit(settings, prober):
    settings.max_targets = 1
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings, prober=prober)) as c:
        login(c)
        assert c.post("/api/targets", json=GW).status_code == 201
        assert c.post("/api/targets", json={**GW, "host": "192.168.2.2"}).status_code == 409


def test_status_series_and_export(client, prober):
    t = client.post("/api/targets", json=GW).json()
    _wait(lambda: client.get("/api/status").json()["targets"][0]["state"] == "up")
    monitor = client.app.state.monitor
    _wait(lambda: monitor.pending_samples == 0 and monitor.last_flush_at is not None)

    st = client.get("/api/status").json()["targets"][0]
    assert st["id"] == t["id"] and st["last"]["rtt_ms"] == 10.0

    s = client.get(f"/api/targets/{t['id']}/series", params={"start": int(time.time()) - 600})
    assert s.status_code == 200
    body = s.json()
    assert body["source"] == "raw" and body["summary"]["count"] >= 1
    assert set(body["series"]) >= {"ts", "median", "p5", "p95", "loss_pct"}

    csv = client.get(f"/api/targets/{t['id']}/export", params={"format": "csv"})
    assert csv.status_code == 200 and csv.text.startswith("ts_ms,rtt_ms,ok,error")
    assert "attachment" in csv.headers["content-disposition"]
    js = client.get(f"/api/targets/{t['id']}/export", params={"format": "json"}).json()
    assert js["target"]["id"] == t["id"] and js["samples"][0]["ok"] is True

    bad = client.get(f"/api/targets/{t['id']}/series", params={"start": 10, "end": 5})
    assert bad.status_code == 422


def test_test_now(client, prober):
    t = client.post("/api/targets", json={**GW, "enabled": False}).json()
    prober.down.add("192.168.2.1")
    r = client.post(f"/api/targets/{t['id']}/test")
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"] == "timeout"


def test_system(client):
    body = client.get("/api/system").json()
    assert body["version"] == __version__
    assert body["retention"] == {"raw_days": 7, "rollup_days": 90}


def test_seed_defaults_once(tmp_path, monkeypatch, caplog):
    import logging

    from fastapi.testclient import TestClient

    # INFO must be enabled: a log `extra` key colliding with a LogRecord attribute only
    # raises when the record is actually built.
    caplog.set_level(logging.INFO)

    import netwatch.main as main_mod

    monkeypatch.setattr(main_mod, "default_route", lambda: DefaultRoute("enp3s0", "192.168.2.1"))
    s = test_settings(tmp_path, seed_defaults=True)
    with TestClient(create_app(s, prober=FakeProber())) as c:
        login(c)
        names = [t["name"] for t in c.get("/api/targets").json()]
        assert names[0] == "Gateway" and "DNS via Gateway" in names and len(names) == 6
        assert "seeded default targets" in caplog.text
        for t in c.get("/api/targets").json():
            c.delete(f"/api/targets/{t['id']}")
    with TestClient(create_app(s, prober=FakeProber())) as c:
        login(c)
        assert c.get("/api/targets").json() == []


def test_metrics_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    s = test_settings(tmp_path, metrics_enabled=True)
    with TestClient(create_app(s, prober=FakeProber())) as c:
        login(c)
        c.post("/api/targets", json=GW)
        _wait(lambda: "netwatch_probe_up{" in c.get("/metrics").text)
        assert 'target="Gateway"' in c.get("/metrics").text


def test_metrics_disabled_by_default(client):
    assert client.get("/metrics").status_code == 404


def test_spa_serving(tmp_path):
    from fastapi.testclient import TestClient

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    s = test_settings(tmp_path, static_dir=static)
    with TestClient(create_app(s, prober=FakeProber())) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/targets/3").text == "<html>app</html>"
        r = c.get("/assets/app.js")
        assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
        assert "nope" not in c.get("/../secret.txt").text
        assert c.get("/api/does-not-exist").status_code == 401  # API needs a session
        login(c)
        assert c.get("/api/does-not-exist").status_code == 404
