from __future__ import annotations

from netwatch.services.events import MONITOR, EventLog
from netwatch.services.integration_health import IntegrationHealth, Poller

MIN = 60_000
T0 = 1_763_200_000_000


class FakeSvc:
    def __init__(self, configured=True, enabled=True):
        self.configured = configured
        self.enabled = enabled
        self.paused = None
        self.last_poll_ms = None
        self.last_ok_ms = None
        self.error = None

    def config(self):
        return {"enabled": self.enabled}

    def poll(self, ts, ok=True, error=None):
        self.last_poll_ms = ts
        if ok:
            self.last_ok_ms, self.error = ts, None
        else:
            self.error = error


def make(store, svc, kv=None, **kw):
    kv = {} if kv is None else kv
    ev = EventLog(store.engine)
    h = IntegrationHealth(
        [Poller("adguard", "AdGuard Home", svc, 60, "adguard")],
        ev,
        lambda k, d=None: kv.get(k, d),
        kv.__setitem__,
        started_ms=T0,
        **kw,
    )
    return h, ev


def row(h, now, key="adguard"):
    return next(r for r in h.rows(now) if r["key"] == key)


def test_poller_states_and_fixes(store):
    svc = FakeSvc(configured=False)
    h, _ = make(store, svc)
    assert row(h, T0)["state"] == "off"
    svc.configured, svc.enabled = True, False
    r = row(h, T0)
    assert r["state"] == "not_polling" and r["fix"]["action"] == "polling"
    svc.enabled = True
    assert row(h, T0 + MIN)["state"] == "waiting"  # just started
    svc.poll(T0 + MIN)
    assert row(h, T0 + 2 * MIN)["state"] == "ok"
    # Failing polls for more than 3 intervals: stale, with the error and a way to the settings.
    for i in range(2, 6):
        svc.poll(T0 + i * MIN, ok=False, error="connection refused")
        h.rows(T0 + i * MIN)  # the service samples every 15 s, so it sees each poll
    r = row(h, T0 + 5 * MIN)
    assert r["state"] == "stale" and r["error"] == "connection refused"
    assert r["detail"].startswith("No good read for 4 min") and r["fix"]["action"] == "admin"
    assert r["reads_24h"] == {"ok": 1, "total": 5}
    svc.paused = "5 failed sign-ins"
    assert row(h, T0 + 5 * MIN)["state"] == "paused"


def test_stale_for_30_minutes_opens_one_event_and_reading_closes_it(store):
    svc = FakeSvc()
    svc.poll(T0)
    h, ev = make(store, svc)
    assert h.check(T0 + MIN) == []
    assert h.check(T0 + 10 * MIN) == []  # stale since now, not yet 30 min
    assert h.check(T0 + 35 * MIN) == []
    assert h.check(T0 + 41 * MIN) == [{"source": "adguard", "action": "opened"}]
    assert h.check(T0 + 50 * MIN) == []  # one event, not one per check
    (e,) = ev.list(0, 10**14, kinds=[MONITOR])
    assert e["title"] == "AdGuard Home has stopped reading" and e["severity"] == "info"
    assert e["open"] and e["start_ms"] == T0 + 10 * MIN
    svc.poll(T0 + 55 * MIN)
    assert h.check(T0 + 56 * MIN) == [{"source": "adguard", "action": "closed"}]
    (e,) = ev.list(0, 10**14, kinds=[MONITOR])
    assert not e["open"]


def test_polling_off_is_not_a_problem(store):
    svc = FakeSvc(enabled=False)
    h, ev = make(store, svc)
    for m in (1, 40, 80):
        h.check(T0 + m * MIN)
    assert ev.list(0, 10**14, kinds=[MONITOR]) == [] and h.view(T0)["problems"] == 0


def test_other_sources(store):
    svc = FakeSvc()
    day = 86_400_000
    jobs = [
        {
            "key": "bufferbloat",
            "label": "Nightly load and speed test",
            "enabled": True,
            "last_ms": T0 - 2 * day,
            "last_ok_ms": T0 - 2 * day,
            "last_error": None,
        },
        {
            "key": "exposure",
            "label": "Nightly exposure scan",
            "enabled": True,
            "last_ms": T0 - 3600_000,
            "last_ok_ms": T0 - day,
            "last_error": "Hub refused",
        },
    ]
    h, _ = make(
        store,
        svc,
        capture=lambda: {"enabled": True, "running": False, "error": "PermissionError"},
        nic=lambda: [{"iface": "eth0", "present": True, "carrier": True, "speed": 1000}],
        nightly=lambda: jobs,
        weekly=lambda: T0 - 10 * day,
        helper=lambda: {"installed": True, "connected": False},
    )
    now = T0 + 30 * day  # long after start, so nothing counts as "just started"
    h.started_ms = T0 - 60 * day
    rows = {r["key"]: r for r in h.rows(now)}
    assert rows["capture"]["state"] == "stale" and "NET_RAW" in rows["capture"]["detail"]
    assert rows["nic"]["state"] == "ok" and "eth0 1000 Mb/s" in rows["nic"]["detail"]
    assert (
        rows["bufferbloat"]["state"] == "stale" and rows["bufferbloat"]["fix"]["label"] == "Run now"
    )
    assert rows["exposure"]["state"] == "stale" and rows["exposure"]["error"] == "Hub refused"
    assert rows["weekly"]["state"] == "stale" and rows["weekly"]["fix"]["action"] == "weekly"
    assert rows["helper"]["state"] == "stale"
    jobs[0]["enabled"] = False
    assert row(h, now, "bufferbloat")["state"] == "off"


def test_api(client):
    r = client.get("/api/admin/integrations/health")
    assert r.status_code == 200
    body = r.json()
    keys = [x["key"] for x in body["rows"]]
    assert keys[:3] == ["deco", "hub", "adguard"]
    assert {"capture", "nic", "bufferbloat", "exposure", "weekly", "helper"} <= set(keys)
    assert all(x["state"] for x in body["rows"])
    deco = body["rows"][0]
    assert deco["state"] == "off" and deco["reads_24h"] is None


def test_api_needs_sign_in(anon_client):
    assert anon_client.get("/api/admin/integrations/health").status_code == 401
