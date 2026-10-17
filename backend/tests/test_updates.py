import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from netwatch.main import create_app
from netwatch.services import updates as up
from netwatch.services.updates import (
    UpdateError,
    UpdateManager,
    UpgradeFolder,
    allowed_by_policy,
    bump_kind,
    in_window,
    parse_releases,
    parse_version,
)

from .conftest import FakeProber, login, test_settings

CURRENT = "0.2.0"
GH = [
    {
        "tag_name": "v0.3.0",
        "name": "NetWatch v0.3.0",
        "body": "### Added\n- Monitoring",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-01T00:00:00Z",
        "html_url": "https://x/0.3.0",
    },
    {
        "tag_name": "v0.2.1",
        "name": "NetWatch v0.2.1",
        "body": "### Fixed\n- Bug",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-20T00:00:00Z",
        "html_url": "https://x/0.2.1",
    },
    {"tag_name": "v0.2.0", "body": "", "draft": False, "prerelease": False},
    {"tag_name": "v0.4.0-rc1", "draft": False, "prerelease": True},
    {"tag_name": "v9.9.9", "draft": True, "prerelease": False},
    {"tag_name": "nightly", "draft": False, "prerelease": False},
]


@pytest.fixture(autouse=True)
def _version(monkeypatch):
    monkeypatch.setattr(up, "__version__", CURRENT)


def gh_client(payload=GH, status=200) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/repos/parabyte-ca/netwatch/releases"
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def connect_agent(folder, when: datetime | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    when = when or datetime.now(UTC)
    (folder / "agent.json").write_text(json.dumps({"last_seen": iso(when), "every_minutes": 1}))


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_versions():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2") is None and parse_version("") is None
    assert bump_kind("0.2.0", "0.2.1") == "patch"
    assert bump_kind("0.2.0", "0.3.0") == "minor"
    assert bump_kind("0.2.0", "1.0.0") == "major"
    assert bump_kind("0.2.0", "0.2.0") is None and bump_kind("0.2.0", "0.1.9") is None


def test_policy_rules():
    assert allowed_by_policy("patch", "patch")
    assert not allowed_by_policy("patch", "minor")
    assert allowed_by_policy("minor", "minor") and allowed_by_policy("minor", "patch")
    assert not allowed_by_policy("minor", "major")
    assert not allowed_by_policy("notify", "patch") and not allowed_by_policy("off", "patch")


def test_window():
    tz = ZoneInfo("America/Toronto")
    assert in_window(datetime(2026, 10, 20, 3, 30, tzinfo=tz), "03:30")
    assert in_window(datetime(2026, 10, 20, 4, 29, tzinfo=tz), "03:30")
    assert not in_window(datetime(2026, 10, 20, 4, 30, tzinfo=tz), "03:30")
    assert not in_window(datetime(2026, 10, 20, 3, 29, tzinfo=tz), "03:30")
    assert in_window(datetime(2026, 10, 21, 0, 10, tzinfo=tz), "23:30")  # crosses midnight


def test_parse_releases_filters_and_sorts():
    rel = parse_releases(GH)
    assert [r["version"] for r in rel] == ["0.3.0", "0.2.1", "0.2.0"]
    assert rel[0]["notes"].startswith("### Added")


# ── folder protocol ───────────────────────────────────────────────────────────


def test_folder_states(tmp_path):
    f = UpgradeFolder(tmp_path)
    now = datetime.now(UTC)
    assert f.state(now.timestamp())["state"] == "idle"
    assert not f.helper_connected(now.timestamp())
    connect_agent(tmp_path, now - timedelta(minutes=2))
    assert f.helper_connected(now.timestamp())
    connect_agent(tmp_path, now - timedelta(minutes=10))
    assert not f.helper_connected(now.timestamp())

    req = f.write_request("update", "0.2.1", "manual", "admin")
    assert f.state(now.timestamp())["state"] == "requested"
    assert f.state(now.timestamp() + 7200)["state"] == "failed"  # agent never picked it up
    (tmp_path / "request.json").unlink()

    status = {"id": req["id"], "state": "running", "started_at": iso(now)}
    (tmp_path / "status.json").write_text(json.dumps(status))
    assert f.state(now.timestamp())["state"] == "running"
    assert f.state(now.timestamp() + 3600)["state"] == "failed"  # stalled
    (tmp_path / "status.json").write_text(json.dumps({**status, "state": "succeeded"}))
    assert f.state(now.timestamp())["state"] == "succeeded"


def test_folder_ignores_symlinks_and_garbage(tmp_path):
    (tmp_path / "secret").write_text('{"last_seen": "2099-01-01T00:00:00Z"}')
    (tmp_path / "agent.json").symlink_to(tmp_path / "secret")
    (tmp_path / "status.json").write_text("not json")
    f = UpgradeFolder(tmp_path)
    assert f.agent() is None and f.status() is None


def test_log_tail_and_history(tmp_path):
    f = UpgradeFolder(tmp_path)
    (tmp_path / "upgrade.log").write_text("line1\n" + "x" * 50_000 + "\nlast line\n")
    tail = f.log_tail(1000)
    assert tail.endswith("last line\n") and len(tail) <= 1000
    lines = [json.dumps({"id": str(i), "result": "succeeded"}) for i in range(30)] + ["junk"]
    (tmp_path / "history.jsonl").write_text("\n".join(lines))
    hist = f.history(5)
    assert [h["id"] for h in hist] == ["29", "28", "27", "26", "25"]


# ── manager ───────────────────────────────────────────────────────────────────


def make_manager(tmp_path, store, **over) -> UpdateManager:
    s = test_settings(tmp_path, update_check=True, **over)
    return UpdateManager(s, store, client=gh_client())


async def test_check_and_summary(tmp_path, store):
    m = make_manager(tmp_path, store)
    await m.check()
    s = m.summary()
    assert s["current"] == CURRENT and s["latest"] == "0.3.0"
    assert [r["version"] for r in s["available"]] == ["0.3.0", "0.2.1"]
    assert [r["kind"] for r in s["available"]] == ["minor", "patch"]
    assert s["policy"] == {"mode": "patch", "window": "03:30"}
    assert s["auto_target"] == "0.2.1"  # patch policy never auto-installs the minor release


async def test_check_failure_keeps_previous_releases(tmp_path, store):
    m = make_manager(tmp_path, store)
    await m.check()
    m._client = gh_client(status=500)
    cache = await m.check()
    assert cache["error"] and len(cache["releases"]) == 3


async def test_check_disabled(tmp_path, store):
    m = make_manager(tmp_path, store)
    m.settings.update_check = False
    with pytest.raises(UpdateError):
        await m.check()


async def test_request_update_rules(tmp_path, store):
    m = make_manager(tmp_path, store)
    await m.check()
    with pytest.raises(UpdateError, match="not connected"):
        m.request_update("0.3.0", reason="manual", by="admin")
    connect_agent(m.folder.path)
    with pytest.raises(UpdateError, match="not a published release"):
        m.request_update("0.9.0", reason="manual", by="admin")
    with pytest.raises(UpdateError, match="not newer"):
        m.request_update("0.2.0", reason="manual", by="admin")
    req = m.request_update("0.3.0", reason="manual", by="admin")
    on_disk = json.loads((m.folder.path / "request.json").read_text())
    assert on_disk == req and req["action"] == "update" and req["version"] == "0.3.0"
    with pytest.raises(UpdateError, match="already in progress"):
        m.request_rollback(by="admin")


async def test_auto_update_in_window_once(tmp_path, store):
    m = make_manager(tmp_path, store)
    await m.check()
    connect_agent(m.folder.path)
    tz = ZoneInfo("America/Toronto")
    outside = datetime.now(tz).replace(hour=12, minute=0).timestamp()
    inside = datetime.now(tz).replace(hour=3, minute=45).timestamp()
    assert m._maybe_auto_update(outside) is None
    req = m._maybe_auto_update(inside)
    assert req["version"] == "0.2.1" and req["reason"] == "auto"
    (m.folder.path / "request.json").unlink()
    assert m._maybe_auto_update(inside) is None  # never retried automatically


async def test_auto_update_respects_mode(tmp_path, store):
    m = make_manager(tmp_path, store)
    await m.check()
    connect_agent(m.folder.path)
    inside = datetime.now(ZoneInfo("America/Toronto")).replace(hour=3, minute=40).timestamp()
    m.set_policy("notify", "03:30")
    assert m._maybe_auto_update(inside) is None
    m.set_policy("minor", "03:30")
    assert m._maybe_auto_update(inside)["version"] == "0.3.0"
    with pytest.raises(UpdateError):
        m.set_policy("always", "03:30")
    with pytest.raises(UpdateError):
        m.set_policy("patch", "25:00")


async def test_tick_checks_daily(tmp_path, store):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=GH)

    m = make_manager(tmp_path, store)
    m._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await m.tick(now=1_000_000)
    await m.tick(now=1_000_000 + 3600)
    assert len(calls) == 1
    await m.tick(now=1_000_000 + 25 * 3600)
    assert len(calls) == 2


# ── API ───────────────────────────────────────────────────────────────────────


def test_updates_api(tmp_path):
    s = test_settings(tmp_path, update_check=True)
    with TestClient(create_app(s, prober=FakeProber(), update_client=gh_client())) as c:
        login(c)
        body = c.get("/api/updates").json()
        assert body["current"] == CURRENT and body["update_available"] is False
        body = c.post("/api/updates/check").json()
        assert body["latest"] == "0.3.0" and body["helper"]["connected"] is False
        assert c.post("/api/updates/apply", json={"version": "0.3.0"}).status_code == 409
        connect_agent(s.upgrade_dir)
        r = c.post("/api/updates/apply", json={"version": "0.3.0"})
        assert r.status_code == 202 and r.json()["requested_by"] == "admin"
        assert c.get("/api/updates").json()["progress"]["state"] == "requested"
        assert c.post("/api/updates/apply", json={"version": "bad"}).status_code == 422
        assert c.put("/api/updates/policy", json={"mode": "minor", "window": "02:15"}).json() == {
            "mode": "minor",
            "window": "02:15",
        }
        assert (
            c.put("/api/updates/policy", json={"mode": "x", "window": "02:15"}).status_code == 422
        )
        (s.upgrade_dir / "upgrade.log").write_text("==> Pulling\n")
        assert c.get("/api/updates/log").text == "==> Pulling\n"
