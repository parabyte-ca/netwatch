from fastapi.testclient import TestClient

from netwatch.auth import LoginThrottle, hash_password, verify_password
from netwatch.main import create_app

from .conftest import ADMIN, CSRF, FakeProber, login, test_settings


def test_hash_roundtrip():
    h = hash_password("s3cret-password")
    assert h.startswith("scrypt$") and "s3cret" not in h
    assert verify_password("s3cret-password", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")  # salted


def test_first_run_setup_flow(anon_client):
    c = anon_client
    c.headers.update(CSRF)
    st = c.get("/api/auth/status").json()
    assert st == {"setup_required": True, "authenticated": False, "username": None}
    assert c.get("/api/targets").status_code == 401
    assert c.post("/api/auth/setup", json={**ADMIN, "password": "short"}).status_code == 422
    assert c.post("/api/auth/setup", json={**ADMIN, "username": "bad name"}).status_code == 422
    r = c.post("/api/auth/setup", json=ADMIN)
    assert r.status_code == 201
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/auth/status").json()["authenticated"] is True
    assert c.get("/api/targets").status_code == 200
    # Setup can only happen once, even from another client.
    other = TestClient(c.app)
    other.headers.update(CSRF)
    assert (
        other.post("/api/auth/setup", json={"username": "evil", "password": "x" * 12}).status_code
        == 409
    )


def test_public_paths_without_session(anon_client):
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/api/auth/status").status_code == 200
    assert anon_client.get("/api/system").status_code == 401
    assert anon_client.get("/api/updates").status_code == 401


def test_login_logout(client):
    client.post("/api/auth/logout")
    assert client.get("/api/targets").status_code == 401
    assert (
        client.post("/api/auth/login", json={**ADMIN, "password": "nope-nope-nope"}).status_code
        == 401
    )
    assert client.post("/api/auth/login", json={**ADMIN, "username": "ADMIN"}).status_code == 200
    assert client.get("/api/targets").status_code == 200


def test_unknown_user_rejected(client):
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever-123"})
    assert r.status_code == 401


def test_login_throttle(client):
    client.post("/api/auth/logout")
    bad = {**ADMIN, "password": "wrong-password"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    r = client.post("/api/auth/login", json=ADMIN)  # locked out even with the right password
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_throttle_unit():
    t = LoginThrottle(max_failures=3, window_s=60, lockout_s=120)
    for i in range(3):
        assert t.retry_after("ip", now=float(i)) == 0
        t.failure("ip", now=float(i))
    assert t.retry_after("ip", now=3.0) == 119
    assert t.retry_after("ip", now=200.0) == 0
    t.failure("other", now=0)
    t.success("other")
    assert t.retry_after("other", now=1) == 0


def test_csrf_and_origin_checks(client):
    body = {"name": "x", "kind": "icmp", "host": "192.0.2.5", "interval_s": 5}
    assert (
        client.post("/api/targets", json=body, headers={"X-Requested-With": ""}).status_code == 403
    )
    r = client.post("/api/targets", json=body, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/targets", json=body).status_code == 201


def test_password_change_revokes_other_sessions(client):
    other = login(TestClient(client.app))
    assert other.get("/api/targets").status_code == 200
    r = client.post(
        "/api/auth/password", json={"current_password": "bad-password", "new_password": "x" * 12}
    )
    assert r.status_code == 403
    r = client.post(
        "/api/auth/password", json={"current_password": ADMIN["password"], "new_password": "short"}
    )
    assert r.status_code == 422
    new = "new-long-password-1"
    r = client.post(
        "/api/auth/password", json={"current_password": ADMIN["password"], "new_password": new}
    )
    assert r.status_code == 200
    assert client.get("/api/targets").status_code == 200  # fresh cookie issued
    assert other.get("/api/targets").status_code == 401  # old session revoked
    other.post("/api/auth/logout")
    assert other.post("/api/auth/login", json={**ADMIN, "password": new}).status_code == 200


def test_metrics_token(tmp_path):
    s = test_settings(tmp_path, metrics_enabled=True, metrics_token="scrape-me")
    with TestClient(create_app(s, prober=FakeProber())) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer scrape-me"}).status_code == 200


def test_reset_password_cli(tmp_path, monkeypatch, capsys):
    import io

    from netwatch import reset_password
    from netwatch.auth import AuthStore
    from netwatch.config import get_settings
    from netwatch.db import make_engine

    monkeypatch.setenv("NETWATCH_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        monkeypatch.setattr("sys.stdin", io.StringIO("first-password-1\n"))
        assert reset_password.main(["--stdin"]) == 0
        assert "created admin" in capsys.readouterr().out
        monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
        assert reset_password.main(["--stdin"]) == 1
        monkeypatch.setattr("sys.stdin", io.StringIO("second-password-2\n"))
        assert reset_password.main(["admin", "--stdin"]) == 0
        monkeypatch.setattr("sys.stdin", io.StringIO("second-password-2\n"))
        assert reset_password.main(["nobody", "--stdin"]) == 1
        store = AuthStore(make_engine(tmp_path / "netwatch.db"))
        assert store.authenticate("admin", "second-password-2") is not None
    finally:
        get_settings.cache_clear()
