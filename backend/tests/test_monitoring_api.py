import json
import time

from fastapi.testclient import TestClient

from netwatch.main import create_app

from .conftest import FakeProber, login, test_settings
from .test_nic import make_iface, setv
from .test_pathtest import MTR
from .test_throughput import OK as IPERF_OK

GW = {"name": "Gateway", "kind": "icmp", "host": "192.168.2.1", "interval_s": 1, "group": "gateway"}
CF = {"name": "Cloudflare", "kind": "icmp", "host": "1.1.1.1", "interval_s": 1, "group": "public"}


def wait(cond, limit_s=6.0):
    end = time.time() + limit_s
    while True:
        v = cond()
        if v:
            return v
        assert time.time() < end, "condition not met in time"
        time.sleep(0.05)


def fake_runner(out_for):
    calls = []

    async def run(cmd, limit_s):
        calls.append(cmd)
        return 0, out_for(cmd), ""

    run.calls = calls
    return run


def test_outage_lifecycle_with_evidence(tmp_path):
    prober = FakeProber()
    s = test_settings(tmp_path, path_test_on_outage=True)
    with TestClient(create_app(s, prober=prober)) as c:
        login(c)
        c.app.state.path_tester.runner = fake_runner(lambda cmd: json.dumps(MTR))
        gw = c.post("/api/targets", json=GW).json()
        cf = c.post("/api/targets", json=CF).json()
        wait(lambda: all(t["state"] == "up" for t in c.get("/api/status").json()["targets"]))

        prober.down.update({"1.1.1.1", "192.168.2.1"})
        active = wait(
            lambda: (
                len(c.get("/api/events/active").json()) == 2 and c.get("/api/events/active").json()
            )
        )
        assert {e["kind"] for e in active} == {"outage"}
        states = {t["id"]: t["state"] for t in c.get("/api/status").json()["targets"]}
        assert states == {gw["id"]: "down", cf["id"]: "down"}

        prober.down.clear()
        wait(lambda: c.get("/api/events/active").json() == [])
        outage = c.get("/api/events", params={"kind": "outage", "target_id": cf["id"]}).json()[0]
        assert outage["duration_ms"] > 0 and outage["details"]["target_name"] == "Cloudflare"

        detail = wait(
            lambda: (d := c.get(f"/api/events/{outage['id']}").json())["path_tests"] and d
        )
        assert detail["target"]["id"] == cf["id"]
        assert [e["details"]["target_name"] for e in detail["concurrent_failures"]] == ["Gateway"]
        assert detail["path_tests"][0]["trigger"] == "outage"
        assert detail["path_tests"][0]["event_id"] == outage["id"]

        series = c.get(
            f"/api/targets/{cf['id']}/series", params={"start": int(time.time()) - 300}
        ).json()
        assert [e["kind"] for e in series["events"]] == ["outage"]
        kinds = {e["kind"] for e in c.get("/api/events").json()}
        assert "monitor" in kinds  # "NetWatch started"
        assert c.get("/api/events", params={"kind": "bogus"}).status_code == 422
        assert c.get("/api/events/99999").status_code == 404


def test_manual_path_test_api(tmp_path):
    s = test_settings(tmp_path)
    with TestClient(create_app(s, prober=FakeProber())) as c:
        login(c)
        c.app.state.path_tester.runner = fake_runner(lambda cmd: json.dumps(MTR))
        t = c.post("/api/targets", json=CF).json()
        r = c.post("/api/path-tests", json={"target_id": t["id"]})
        assert r.status_code == 201 and r.json()["first_loss_hop"] == 3
        assert c.post("/api/path-tests", json={"host": "-rf"}).status_code == 422
        assert c.post("/api/path-tests", json={}).status_code == 422
        assert c.post("/api/path-tests", json={"target_id": 999}).status_code == 404
        r = c.post("/api/path-tests", json={"host": "8.8.8.8"}).json()
        assert c.get(f"/api/path-tests/{r['id']}").json()["host"] == "8.8.8.8"
        assert len(c.get("/api/path-tests").json()) == 2
        assert len(c.get("/api/path-tests", params={"target_id": t["id"]}).json()) == 1


def test_iperf_api(tmp_path):
    s = test_settings(tmp_path)
    with TestClient(create_app(s, prober=FakeProber())) as c:
        login(c)
        c.app.state.throughput.runner = fake_runner(lambda cmd: json.dumps(IPERF_OK))
        body = {"name": "PC", "host": "192.168.2.2", "kind": "lan", "duration_s": 5}
        assert c.post("/api/iperf/servers", json={**body, "interval_min": 5}).status_code == 422
        assert c.post("/api/iperf/servers", json={**body, "host": "bad host"}).status_code == 422
        srv = c.post("/api/iperf/servers", json=body).json()
        assert srv["port"] == 5201 and srv["interval_min"] == 0
        upd = c.put(f"/api/iperf/servers/{srv['id']}", json={**body, "interval_min": 60}).json()
        assert upd["interval_min"] == 60
        results = c.post(f"/api/iperf/servers/{srv['id']}/run").json()
        assert [r["direction"] for r in results] == ["download", "upload"]
        assert len(c.get("/api/iperf/tests").json()) == 2
        assert c.delete(f"/api/iperf/servers/{srv['id']}").status_code == 204
        assert c.post(f"/api/iperf/servers/{srv['id']}/run").status_code == 404
        assert c.get("/api/iperf/tests").json()[0]["server_id"] is None  # history kept


def test_nic_api(tmp_path, monkeypatch):
    sys_root = tmp_path / "sys"
    d = make_iface(sys_root, "enp3s0")
    s = test_settings(tmp_path, nic_interfaces="enp3s0", nic_poll_s=0.5)
    with TestClient(create_app(s, prober=FakeProber())) as c:
        login(c)
        wait(lambda: c.get("/api/nic").json()["interfaces"][0].get("present"))
        nic = c.get("/api/nic").json()["interfaces"][0]
        assert nic["iface"] == "enp3s0" and nic["speed"] == 2500
        setv(d, operstate="down", carrier="0")
        wait(lambda: c.get("/api/events/active").json())
        ev = c.get("/api/events/active").json()[0]
        assert ev["kind"] == "nic_link" and ev["iface"] == "enp3s0"
        assert c.get("/api/nic/enp3s0/history").status_code == 200
        assert c.get("/api/nic/eth9/history").status_code == 404


def test_retention_purges_phase3_tables(tmp_path):
    s = test_settings(tmp_path, event_retention_days=7)
    with TestClient(create_app(s, prober=FakeProber())) as c:
        login(c)
        st = c.app.state
        old = int((time.time() - 30 * 86400) * 1000)
        st.events.instant("monitor", "info", old, "ancient")
        import asyncio

        asyncio.run(st.monitor.maintenance(purge=True))
        assert all(e["title"] != "ancient" for e in st.events.list(0, 2**62))
