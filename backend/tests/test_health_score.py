from __future__ import annotations

import time

from netwatch.services.events import OUTAGE
from netwatch.services.health import compute, label

from .conftest import make_target


def report(**over) -> dict:
    r = {
        "summary": {
            "incidents": 0,
            "isp_incidents": 0,
            "isp_downtime_ms": 0,
            "isp_downtime": "0 s",
            "isp_availability_pct": 100.0,
            "longest": "0 s",
        },
        "targets": [
            {"layer": "internet", "loss_pct": 0.0, "p95_ms": 20.0},
            {"layer": "gateway", "loss_pct": 5.0, "p95_ms": 2.0},  # not an internet target
        ],
        "line": {},
        "incidents": [],
        "patterns": [],
        "speeds": [],
    }
    r.update(over)
    return r


def test_perfect_week_scores_100_and_missing_parts_are_left_out():
    h = compute(report())
    assert h["score"] == 100 and h["label"] == "Excellent"
    assert {c["key"] for c in h["components"]} == {"internet", "quality", "home"}
    assert h["highlights"][0].startswith("No internet outages")


def test_outages_bloat_fibre_and_exposure_lower_the_score():
    r = report(
        summary={
            "incidents": 3,
            "isp_incidents": 2,
            "isp_downtime_ms": 1_200_000,
            "isp_downtime": "20 min",
            "isp_availability_pct": 99.8,
            "longest": "15 min",
        },
        line={"bufferbloat": {"grade": "C"}, "rx_dbm_min": -26.0, "hub_restarts": 1},
        incidents=[{"causes": [{"layer": "mesh"}], "wifi_events": [{}, {}]}],
        patterns=[{"title": "Failures repeat about every 24 h"}],
    )
    exposure = {"latest": {"id": 1}, "findings": [{"severity": "warning"}]}
    h = compute(r, exposure)
    by = {c["key"]: c for c in h["components"]}
    assert by["internet"]["score"] == 80
    assert by["load"]["score"] == 60
    assert by["line"]["score"] == 45  # -26 dBm -> 60, one restart -15
    assert by["home"]["score"] == 79  # one mesh incident, two Wi-Fi events
    assert by["exposure"]["score"] == 85
    assert 60 < h["score"] < 85 and h["label"] in ("Fair", "Good")
    assert "dropped 2 times for 20 min" in h["highlights"][0]
    assert any("Weakest area: fibre line" in x for x in h["highlights"])
    assert any("Pattern: Failures repeat" in x for x in h["highlights"])
    assert label(None) == "No data" and label(40) == "Poor"


def test_score_and_weekly_report_api(client):
    st = client.app.state
    t = make_target(st.store, name="Cloudflare", host="1.1.1.1", group="public")
    h = st.health
    week = h.last_full_week()
    start, end = h.week_bounds(week)
    st.events.open(OUTAGE, "critical", start + 3600_000, "Cloudflare unreachable",
                   target_id=t["id"], end_ms=start + 3600_000 + 600_000)  # fmt: skip
    live = client.get("/api/score?days=7").json()
    assert live["days"] == 7 and live["score"] is not None
    assert client.get("/api/weekly-reports").json()["reports"] == []
    r = client.post("/api/weekly-reports", json={}).json()
    assert r["week_start"] == week and r["incident_count"] == 1 and r["score"] < 100
    assert client.post("/api/weekly-reports", json={"week_start": "2026-10-14"}).status_code == 422
    lst = client.get("/api/weekly-reports").json()
    assert lst["reports"][0]["week_start"] == week and lst["last_full_week"] == week
    md = client.get(f"/api/weekly-reports/{week}?format=md")
    assert md.status_code == 200 and "Health score:" in md.text
    assert client.get("/api/weekly-reports/2001-01-01").status_code == 404
    # Next week's report compares with this one.
    nxt = h.generate(
        time.strftime("%Y-%m-%d", time.localtime(end / 1000 + 3600)), now_ms=end + 8 * 86_400_000
    )
    assert nxt["previous_score"] == r["score"]
