from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from netwatch.probes import ProbeResult
from netwatch.services.diagnosis import Diagnoser, find_patterns
from netwatch.services.events import EventLog
from netwatch.services.netinfo import DefaultRoute
from netwatch.services.pathtest import PathStore

from .conftest import make_target

GW = "192.168.2.1"
NOW = int(time.time() * 1000) // 60_000 * 60_000
T0 = NOW - 3600_000  # incidents start an hour ago


@pytest.fixture
def net(store):
    """A home network: Hub, two public anchors, DNS via Hub and via 1.1.1.1, two Decos."""
    t = {
        "hub": make_target(store, name="Gateway", host=GW, group="gateway"),
        "cf": make_target(store, name="Cloudflare", host="1.1.1.1", group="public"),
        "goog": make_target(store, name="Google", host="8.8.8.8", group="public"),
        "dns_hub": make_target(store, name="DNS via Gateway", kind="dns", host=GW, group="dns",
                               dns_query="www.google.com", dns_rdtype="A", interval_s=10),
        "dns_cf": make_target(store, name="DNS via Cloudflare", kind="dns", host="1.1.1.1",
                              group="dns", dns_query="www.google.com", dns_rdtype="A",
                              interval_s=10),
        "deco1": make_target(store, name="Deco Living", host="192.168.2.10", group="mesh"),
        "deco2": make_target(store, name="Deco Office", host="192.168.2.11", group="mesh"),
    }  # fmt: skip
    return t


@pytest.fixture
def diag(store):
    events = EventLog(store.engine)
    paths = PathStore(store.engine)
    d = Diagnoser(store, events, paths, gateway=lambda: DefaultRoute("br0", GW))
    return d


def samples(store, target, start_ms, end_ms, ok=True, step_ms=2000):
    store.insert_samples(
        ProbeResult(target["id"], ts, ok, rtt_ms=5.0 if ok else None, error=None if ok else "x")
        for ts in range(start_ms, end_ms, step_ms)
    )


def outage(diag, target, start_ms, end_ms, kind="outage"):
    eid = diag.events.open(kind, "critical", start_ms, f"{target['name']} unreachable",
                           target_id=target["id"], end_ms=end_ms)  # fmt: skip
    return eid


def scenario(store, diag, net, failing, *, dur_ms=120_000, kind="outage"):
    """`failing` targets fail for dur_ms from T0; every other target answers throughout."""
    for key, t in net.items():
        samples(store, t, T0 - 300_000, T0, ok=True)
        samples(store, t, T0, T0 + dur_ms, ok=key not in failing)
        samples(store, t, T0 + dur_ms, T0 + dur_ms + 300_000, ok=True)
        if key in failing:
            outage(diag, t, T0, T0 + dur_ms, kind)
    return diag.incidents(T0 - 600_000, NOW, NOW)


def top(incidents):
    assert len(incidents) == 1, incidents
    return incidents[0].causes[0]


def test_bell_outage_when_hub_stays_up(store, diag, net):
    c = top(scenario(store, diag, net, {"cf", "goog", "dns_cf"}))
    assert c.id == "isp" and c.layer == "isp" and c.confidence >= 85
    assert any("Home Hub kept answering" in e for e in c.evidence)


def test_path_test_beyond_hub_raises_confidence(store, diag, net):
    diag.paths.insert({
        "ts_ms": T0 + 10_000, "host": "1.1.1.1", "trigger": "outage", "status": "ok",
        "hop_count": 4, "reached": False, "first_loss_hop": 3, "path_changed": False,
        "hops": json.dumps([{"hop": 1, "host": GW}, {"hop": 2, "host": "10.11.0.1"},
                            {"hop": 3, "host": "64.230.1.1"}, {"hop": 4, "host": None}]),
    })  # fmt: skip
    c = top(scenario(store, diag, net, {"cf", "goog"}))
    assert c.id == "isp" and c.confidence >= 90
    assert any("hop 3 (64.230.1.1)" in e for e in c.evidence)


def test_everything_down_points_at_lan_or_hub(store, diag, net):
    incs = scenario(store, diag, net, set(net), dur_ms=180_000)
    causes = {c.id for c in incs[0].causes}
    assert incs[0].causes[0].id == "lan"
    assert "hub_restart" in causes  # 3 minutes: about a Hub reboot


def test_nic_link_drop_wins(store, diag, net):
    diag.events.open("nic_link", "critical", T0, "Link down on enp3s0", iface="enp3s0",
                     end_ms=T0 + 60_000)  # fmt: skip
    c = top(scenario(store, diag, net, set(net)))
    assert c.id == "host_link" and c.confidence >= 90


def test_hub_dns_only(store, diag, net):
    c = top(scenario(store, diag, net, {"dns_hub"}, kind="degraded"))
    assert c.id == "hub_dns" and c.confidence >= 80


def test_single_deco(store, diag, net):
    c = top(scenario(store, diag, net, {"deco2"}))
    assert c.id == "mesh_node" and "Deco Office" in c.title


def test_single_destination(store, diag, net):
    c = top(scenario(store, diag, net, {"goog"}))
    assert c.id == "destination"


def test_ip_conflict_on_failing_host(store, diag, net):
    diag.events.instant("anomaly", "critical", T0 - 30_000, "Duplicate IP 192.168.2.11",
                        details={"type": "duplicate_ip", "ip": "192.168.2.11",
                                 "macs": ["aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02"]})  # fmt: skip
    c = top(scenario(store, diag, net, {"deco2"}))
    assert c.id == "ip_conflict" and c.confidence == 90


def test_failures_close_together_are_one_incident(store, diag, net):
    outage(diag, net["cf"], T0, T0 + 60_000)
    outage(diag, net["goog"], T0 + 90_000, T0 + 120_000)  # 30 s after: same incident
    outage(diag, net["goog"], T0 + 600_000, T0 + 660_000)  # 8 min later: a new one
    incs = diag.incidents(T0 - 60_000, NOW, NOW)
    assert [len(i.events) for i in incs] == [2, 1]
    assert incs[0].end_ms == T0 + 120_000


def test_open_incident(store, diag, net):
    outage(diag, net["cf"], NOW - 60_000, None)
    (inc,) = diag.incidents(NOW - 600_000, NOW, NOW)
    assert inc.end_ms is None and inc.as_dict(NOW)["open"]


def test_unknown_when_no_comparison(store, diag):
    t = make_target(store, name="Lonely", host="192.0.2.50", group="custom")
    outage(diag, t, T0, T0 + 60_000)
    c = top(diag.incidents(T0 - 60_000, NOW, NOW))
    assert c.confidence <= 70


def test_interval_and_time_of_day_patterns():
    tz = ZoneInfo("America/Toronto")
    base = 1_790_000_000_000
    starts = [base + i * 6 * 3600_000 + (i % 2) * 30_000 for i in range(6)]
    kinds = {p["kind"]: p for p in find_patterns(starts, tz)}
    assert kinds["interval"]["period_s"] == pytest.approx(6 * 3600, abs=60)
    assert "6 h" in kinds["interval"]["title"]
    # Every night between 02:10 and 03:50 local time, at irregular minutes.
    nightly = [
        int(datetime(2026, 10, 1 + i * 2, 2, 10, tzinfo=tz).timestamp() * 1000)
        + (i * 37 % 100) * 60_000
        for i in range(6)
    ]
    tod = {p["kind"]: p for p in find_patterns(nightly, tz)}
    assert tod["time_of_day"]["share"] == 100
    assert tod["time_of_day"]["from_hour"] == 2


def test_no_patterns_from_random_times():
    tz = ZoneInfo("America/Toronto")
    base = 1_790_000_000_000
    starts = [base + x * 60_000 for x in (0, 37, 211, 980, 1500, 3333, 4000)]
    assert [p for p in find_patterns(starts, tz) if p["kind"] == "interval"] == []


# ── API ───────────────────────────────────────────────────────────────────────


def test_diagnosis_api_and_report(client):
    st = client.app.state
    hub = make_target(st.store, name="Gateway", host=GW, group="gateway")
    cf = make_target(st.store, name="Cloudflare", host="1.1.1.1", group="public")
    goog = make_target(st.store, name="Google", host="8.8.8.8", group="public")
    for t in (hub, cf, goog):
        samples(st.store, t, T0 - 120_000, T0 + 240_000, ok=t is hub)
    e1 = st.events.open("outage", "critical", T0, "Cloudflare unreachable", target_id=cf["id"],
                        end_ms=T0 + 120_000)  # fmt: skip
    st.events.open("outage", "critical", T0 + 5_000, "Google unreachable", target_id=goog["id"],
                   end_ms=T0 + 120_000)  # fmt: skip

    body = client.get("/api/diagnosis").json()
    layers = {n["layer"]: n for n in body["topology"]}
    assert layers["gateway"]["members"][0]["name"] == "Gateway"
    assert layers["isp"]["inferred"] and layers["lan"]["parent"] == "host"
    (inc,) = body["incidents"]
    assert inc["causes"][0]["id"] == "isp"
    assert {f["name"] for f in inc["failing"]} == {"Cloudflare", "Google"}

    r = client.get(f"/api/diagnosis/event/{e1}")
    assert r.status_code == 200 and r.json()["id"] == inc["id"]
    assert client.get("/api/diagnosis/event/999999").status_code == 404

    rep = client.get("/api/report", params={"scope": "isp"}).json()
    assert rep["summary"]["isp_incidents"] == 1
    assert rep["incidents"][0]["cause"].startswith("Bell network outage")
    assert "Home Hub stayed reachable" in " ".join(rep["ruled_out"])
    md = client.get("/api/report", params={"format": "md"})
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert "# Network incident report" in md.text and "| Cloudflare" in md.text
    assert client.get("/api/report", params={"start": 10, "end": 5}).status_code == 422


def test_diagnosis_requires_login(anon_client):
    assert anon_client.get("/api/diagnosis").status_code == 401
    assert anon_client.get("/api/report").status_code == 401


def test_window_stats_combines_rollups_and_recent_raw(store, diag, net):
    """Long ranges: rollups up to the watermark, raw samples for the minutes after it."""
    t = net["cf"]
    old = NOW - 10 * 86_400_000  # older than raw retention: only rollups count
    samples(store, t, old, old + 60_000, ok=True)
    store._rollup_range(old // 1000, old // 1000 + 60)
    store.kv_set("rollup_watermark", (NOW - 600_000) // 1000)
    samples(store, t, NOW - 300_000, NOW - 240_000, ok=False)  # after the watermark: raw
    st = diag.window_stats(t["id"], old - 60_000, NOW, NOW)
    assert st["count"] == 30 + 30 and st["lost"] == 30
