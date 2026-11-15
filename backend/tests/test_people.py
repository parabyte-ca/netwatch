from __future__ import annotations

import pytest
from sqlalchemy import update

from netwatch.db import devices
from netwatch.services.discovery.inventory import DeviceStore
from netwatch.services.events import PRESENCE, EventLog
from netwatch.services.people import PeopleError, PeopleStore, PresenceService

from .conftest import CSRF

MIN = 60_000
T0 = 1_763_200_000_000  # a fixed "now" for the tests


@pytest.fixture
def env(store):
    kv: dict = {}
    events = EventLog(store.engine)
    people = PeopleStore(store.engine)
    svc = PresenceService(people, events, lambda k, d=None: kv.get(k, d), kv.__setitem__)
    devs = DeviceStore(store.engine)
    return svc, people, devs, events, kv


def add_device(devs: DeviceStore, mac: str, seen_ms: int, merged_into: int | None = None) -> int:
    return devs.insert(
        {"mac": mac, "first_seen_ms": T0 - 86_400_000, "last_seen_ms": seen_ms,
         "merged_into": merged_into}
    )  # fmt: skip


def seen(store, device_id: int, ms: int) -> None:
    with store.engine.begin() as conn:
        conn.execute(update(devices).where(devices.c.id == device_id).values(last_seen_ms=ms))


def presence_events(events: EventLog) -> list[dict]:
    return sorted(events.list(0, 10**14, kinds=[PRESENCE]), key=lambda e: e["start_ms"])


def test_sleeping_phone_does_not_flap_then_leaves_and_comes_back(store, env):
    svc, people, devs, events, _ = env
    phone = add_device(devs, "aa:00:00:00:00:01", T0)
    sam = people.create("Sam", "sky", [phone])
    assert svc.tick(T0) == []  # newly added: no event, just the current state
    assert svc.status()[0]["home"] is True
    assert svc.status()[0]["since_ms"] is None  # not known when they arrived
    # Wi-Fi asleep for 12 minutes at a time, under the 15-minute threshold: still home.
    for i in range(1, 6):
        t = T0 + i * 12 * MIN
        assert svc.tick(t) == []
        seen(store, phone, t)
    assert presence_events(events) == []
    last = T0 + 5 * 12 * MIN
    # Gone for 16 minutes: left, dated by the last sighting.
    (ch,) = svc.tick(last + 16 * MIN)
    assert ch == {"person_id": sam["id"], "action": "left", "ts_ms": last}
    assert svc.status()[0]["home"] is False
    # Seen again: arrived at once.
    seen(store, phone, last + 90 * MIN)
    (ch,) = svc.tick(last + 90 * MIN + 30_000)
    assert ch["action"] == "arrived" and ch["ts_ms"] == last + 90 * MIN
    titles = [e["title"] for e in presence_events(events)]
    assert titles[0].startswith("Sam left at ") and titles[1].startswith("Sam arrived home at ")
    assert all(e["severity"] == "info" for e in presence_events(events))


def test_private_mac_siblings_count(store, env):
    svc, people, devs, _, _ = env
    primary = add_device(devs, "aa:00:00:00:00:02", T0 - 60 * MIN)
    sibling = add_device(devs, "da:00:00:00:00:02", T0 - 60 * MIN, merged_into=primary)
    # Picking the sibling stores the primary.
    alex = people.create("Alex", "rose", [sibling])
    assert alex["device_ids"] == [primary]
    svc.tick(T0)
    assert svc.status()[0]["home"] is False
    seen(store, sibling, T0)  # the phone's new private address
    (ch,) = svc.tick(T0 + 30_000)
    assert ch["action"] == "arrived"


def test_restart_does_not_invent_arrivals(store, env):
    svc, people, devs, events, kv = env
    phone = add_device(devs, "aa:00:00:00:00:03", T0)
    people.create("Sam", "sky", [phone])
    svc.tick(T0)
    # A new service with the same kv (a restart) sees the same state and records nothing.
    again = PresenceService(people, events, lambda k, d=None: kv.get(k, d), kv.__setitem__)
    assert again.tick(T0 + MIN) == []
    assert presence_events(events) == []


def test_away_after_setting_and_validation(store, env):
    svc, people, devs, _, _ = env
    assert svc.settings() == {"away_after_min": 15}
    svc.set_settings(30)
    phone = add_device(devs, "aa:00:00:00:00:04", T0)
    people.create("Sam", "sky", [phone])
    svc.tick(T0)
    assert svc.tick(T0 + 20 * MIN) == []  # 20 min is under 30
    assert svc.tick(T0 + 31 * MIN)[0]["action"] == "left"
    with pytest.raises(PeopleError):
        svc.set_settings(2)


def test_person_validation_and_delete_removes_their_events(store, env):
    svc, people, devs, events, _ = env
    phone = add_device(devs, "aa:00:00:00:00:05", T0)
    other = add_device(devs, "aa:00:00:00:00:06", T0)
    sam = people.create("Sam", "sky", [phone])
    kim = people.create("Kim", "lime", [other])
    with pytest.raises(PeopleError, match="already"):
        people.create(" sam ", "rose")
    with pytest.raises(PeopleError, match="unknown device"):
        people.create("Lee", "rose", [9999])
    with pytest.raises(PeopleError, match="colour"):
        people.create("Lee", "plaid")
    svc.tick(T0)
    svc.tick(T0 + 20 * MIN)  # both left
    assert len(presence_events(events)) == 2
    assert people.delete(sam["id"]) == 1
    left = presence_events(events)
    assert [e["details"]["person"] for e in left] == ["Kim"]
    assert [p["name"] for p in people.list()] == ["Kim"]
    assert people.update(kim["id"], device_ids=[])["device_ids"] == []


def test_person_without_devices_is_unknown(store, env):
    svc, people, devs, events, _ = env
    guest = people.create("Guest", "slate")
    svc.tick(T0)
    assert svc.status()[0]["home"] is None
    # Giving them a device that is on the network now is not an arrival.
    phone = add_device(devs, "aa:00:00:00:00:12", T0)
    people.update(guest["id"], device_ids=[phone])
    assert svc.tick(T0 + MIN) == []
    assert svc.status()[0]["home"] is True and presence_events(events) == []


def test_history_days_and_week_summary(store, env):
    svc, people, devs, _, _ = env
    phone = add_device(devs, "aa:00:00:00:00:07", T0)
    sam = people.create("Sam", "sky", [phone])
    svc.tick(T0)
    svc.tick(T0 + 20 * MIN)  # left at T0
    seen(store, phone, T0 + 3 * 3600_000)
    svc.tick(T0 + 3 * 3600_000 + 1000)  # arrived 3 h later
    h = svc.history(sam["id"], days=2, now_ms=T0 + 4 * 3600_000)
    assert [e["action"] for e in h["events"]] == ["left", "arrived"]
    assert len(h["days"]) == 2
    spans = [s for d in h["days"] for s in d["spans"]]
    away = [s for s in spans if s["home"] is False]
    assert away and away[0]["start_ms"] == T0 and away[-1]["end_ms"] == T0 + 3 * 3600_000
    assert svc.week_summary(T0 - 86_400_000, T0 + 86_400_000) == [{"name": "Sam", "arrivals": 1}]


def test_purge_keeps_a_persons_devices(store, env):
    from netwatch.services.discovery.inventory import DeviceStore as DS

    _, people, devs, _, _ = env
    old = add_device(devs, "aa:00:00:00:00:08", T0 - 400 * 86_400_000)
    stray = add_device(devs, "aa:00:00:00:00:09", T0 - 400 * 86_400_000)
    people.create("Sam", "sky", [old])
    assert DS(store.engine).purge(T0) == 1
    assert devs.get(old) is not None and devs.get(stray) is None


def test_people_api(client):
    st = client.app.state
    did = st.discovery.inventory.store.insert(
        {"mac": "aa:00:00:00:00:10", "first_seen_ms": 0, "last_seen_ms": 0}
    )
    r = client.post("/api/people", json={"name": "Sam", "colour": "sky", "device_ids": [did]},
                    headers=CSRF)  # fmt: skip
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    no_csrf = {"X-Requested-With": ""}
    r = client.post("/api/people", json={"name": "X", "colour": "sky"}, headers=no_csrf)
    assert r.status_code == 403
    assert client.post("/api/people", json={"name": "Sam", "colour": "sky"},
                       headers=CSRF).status_code == 422  # fmt: skip
    body = client.get("/api/people").json()
    assert body["people"][0]["name"] == "Sam" and body["settings"] == {"away_after_min": 15}
    assert body["people"][0]["home"] is False
    r = client.patch(f"/api/people/{pid}", json={"name": "Samira"}, headers=CSRF)
    assert r.json()["name"] == "Samira"
    h = client.get(f"/api/people/{pid}/history?days=7").json()
    assert h["person"]["name"] == "Samira"
    assert h["devices"] == [{"id": did, "label": "aa:00:00:00:00:10"}]
    assert client.get("/api/people/999/history").status_code == 404
    assert client.put("/api/people-settings", json={"away_after_min": 3},
                      headers=CSRF).status_code == 422  # fmt: skip
    assert client.put("/api/people-settings", json={"away_after_min": 20},
                      headers=CSRF).json() == {"away_after_min": 20}  # fmt: skip
    assert client.delete(f"/api/people/{pid}", headers=CSRF).json() == {"events_deleted": 0}
    assert client.get("/api/people").json()["people"] == []
    log = [e["title"] for e in st.events.list(0, 10**14, kinds=["security"])]
    assert any("who's home: Sam" in t for t in log)


def test_people_api_needs_sign_in(anon_client):
    assert anon_client.get("/api/people").status_code == 401


def test_weekly_report_lists_arrivals(client):
    import time

    st = client.app.state
    now = int(time.time() * 1000)
    did = st.discovery.inventory.store.insert(
        {"mac": "aa:00:00:00:00:11", "first_seen_ms": 0, "last_seen_ms": now}
    )
    person = st.presence.people.create("Sam", "sky", [did])
    st.events.instant(PRESENCE, "info", now - 60_000, "Sam arrived home at 08:00",
                      details={"person_id": person["id"], "action": "arrived"})  # fmt: skip
    rep = st.health.generate(st.health.last_full_week(now + 7 * 86_400_000), now_ms=now)
    assert rep["people"] == [{"name": "Sam", "arrivals": 1}]
    assert "## Who's home" in rep["markdown"] and "- Sam: arrived home 1 time" in rep["markdown"]
