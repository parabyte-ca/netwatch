from __future__ import annotations

from netwatch.services.adguard.service import AdGuardStore
from netwatch.services.events import DNS, KINDS


def test_store_roundtrip_and_purge(store):
    s = AdGuardStore(store.engine)
    s.add({"ts_ms": 1000, "reachable": True, "protection": True, "queries": 10, "blocked": 2,
           "servfail": 0, "errors": 0, "avg_ms": 12.5, "p95_ms": 30.0, "source": "querylog",
           "upstreams": {"tls://1.1.1.1": {"n": 8, "avg_ms": 14.0, "fail": 0}},
           "details": {"top_clients": [["192.168.2.20", 6]]}})  # fmt: skip
    s.add({"ts_ms": 2000, "reachable": False, "source": "stats"})
    rows = s.history(0, 10_000)
    assert [r["ts_ms"] for r in rows] == [1000, 2000]
    assert rows[0]["upstreams"]["tls://1.1.1.1"]["n"] == 8
    assert rows[1]["upstreams"] == {} and rows[1]["protection"] is None
    assert s.latest()["ts_ms"] == 2000
    assert s.purge(1500) == 1 and s.history(0, 10_000)[0]["ts_ms"] == 2000
    assert DNS == "dns" and DNS in KINDS
