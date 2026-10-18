"""Incident report for ISP support: what failed, when, for how long, and the evidence."""

from __future__ import annotations

import time
from datetime import datetime

from .. import __version__
from .diagnosis import INTERNET, ISP, LAYER_LABEL, Diagnoser, Incident, target_layer
from .stats import loss_pct
from .throughput import ThroughputStore

ISP_LAYERS = (ISP, INTERNET)


def _fmt_dur(ms: int) -> str:
    s = round(ms / 1000)
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def build_report(
    diag: Diagnoser,
    throughput: ThroughputStore,
    start_ms: int,
    end_ms: int,
    *,
    scope: str = "all",
    now_ms: int | None = None,
    hub_store=None,
    bufferbloat_store=None,
) -> dict:
    now = now_ms or int(time.time() * 1000)
    end_ms = min(end_ms, now)
    incidents = diag.incidents(start_ms, end_ms, now)
    if scope == "isp":
        incidents = [i for i in incidents if i.causes and i.causes[0].layer in ISP_LAYERS]
    route = diag.gateway()
    gw = route.gateway if route else None

    def local(ms: int) -> str:
        return diag.local(ms).strftime("%Y-%m-%d %H:%M:%S %Z")

    targets = []
    for t in diag.store.list_targets():
        st = diag.window_stats(t["id"], start_ms, end_ms, now)
        if not st["count"]:
            continue
        targets.append(
            {
                "name": t["name"],
                "host": t["host"],
                "kind": t["kind"],
                "layer": target_layer(t, gw),
                "layer_label": LAYER_LABEL[target_layer(t, gw)],
                "probes": st["count"],
                "loss_pct": loss_pct(st),
                "median_ms": st["median"],
                "p95_ms": st["p95"],
            }
        )

    isp_incidents = [i for i in incidents if i.causes and i.causes[0].layer in ISP_LAYERS]
    isp_down_ms = sum(  # only the part inside the reporting period
        max(0, min(i.end_ms or now, end_ms) - max(i.start_ms, start_ms))
        for i in isp_incidents
        if any(e["kind"] == "outage" for e in i.events)
    )
    span = max(end_ms - start_ms, 1)
    tests = [
        t
        for t in throughput.list_tests(start_ms=start_ms, limit=1000)
        if t["ts_ms"] < end_ms and t["status"] == "ok"
    ]
    speed: dict[str, dict] = {}
    for t in tests:
        key = f"{t['kind']}:{t['direction']}"
        s = speed.setdefault(key, {"kind": t["kind"], "direction": t["direction"], "values": []})
        s["values"].append(t["bits_per_second"])
    speeds = [
        {
            "kind": s["kind"],
            "direction": s["direction"],
            "tests": len(s["values"]),
            "avg_bps": sum(s["values"]) / len(s["values"]),
            "min_bps": min(s["values"]),
            "max_bps": max(s["values"]),
        }
        for s in speed.values()
    ]

    nic_link = diag.events.list(start_ms, end_ms, kinds=["nic_link", "nic_errors"], limit=500)
    hub_ok_during_isp = sum(
        1 for i in isp_incidents if any(h["layer"] == "gateway" for h in i.healthy.values())
    )
    ruled_out = []
    if not nic_link:
        ruled_out.append(
            "The TrueNAS network link stayed up with no link drops or interface errors."
        )
    if isp_incidents:
        ruled_out.append(
            f"The Home Hub stayed reachable on the local network during {hub_ok_during_isp} of "
            f"{len(isp_incidents)} incidents attributed to the Bell network."
        )

    line = _line_evidence(diag, hub_store, bufferbloat_store, start_ms, end_ms, local)
    if line.get("hub_restarts") is not None and line["hub_restarts"] == 0:
        ruled_out.append("The Home Hub did not restart during the period (its own uptime).")

    rows = [_incident_row(i, now, local) for i in incidents]
    report = {
        "generated_at": local(now),
        "generated_ms": now,
        "version": __version__,
        "period": {
            "start": local(start_ms),
            "end": local(end_ms),
            "start_ms": start_ms,
            "end_ms": end_ms,
        },
        "scope": scope,
        "gateway": gw,
        "summary": {
            "incidents": len(incidents),
            "isp_incidents": len(isp_incidents),
            "isp_downtime_ms": isp_down_ms,
            "isp_downtime": _fmt_dur(isp_down_ms),
            "isp_availability_pct": round(100 * (1 - isp_down_ms / span), 3),
            "longest": _fmt_dur(max((i.duration_ms(now) for i in incidents), default=0)),
        },
        "patterns": diag.patterns(incidents),
        "targets": targets,
        "speeds": speeds,
        "line": line,
        "ruled_out": ruled_out,
        "incidents": rows,
    }
    report["markdown"] = render_markdown(report)
    return report


def _incident_row(i: Incident, now: int, local) -> dict:
    top = i.causes[0] if i.causes else None
    return {
        **i.as_dict(now),
        "start": local(i.start_ms),
        "end": local(i.end_ms) if i.end_ms else "ongoing",
        "duration": _fmt_dur(i.duration_ms(now)),
        "cause": top.title if top else "Unknown",
        "confidence": top.confidence if top else 0,
        "path_hops": [
            {
                "host": t["host"],
                "ts": local(t["ts_ms"]),
                "first_loss_hop": t.get("first_loss_hop"),
                "hops": [
                    {
                        "hop": h.get("hop"),
                        "host": h.get("host"),
                        "loss_pct": h.get("loss_pct"),
                        "avg": h.get("avg"),
                    }
                    for h in (t.get("hops") or [])[:12]
                ],
            }
            for t in i.path_tests[:2]
        ],
    }


def _ms(v) -> str:
    return "—" if v is None else f"{v:.1f} ms"


def _bps(v: float) -> str:
    return f"{v / 1e9:.2f} Gb/s" if v >= 1e9 else f"{v / 1e6:.0f} Mb/s"


def render_markdown(r: dict) -> str:
    s = r["summary"]
    scope = "incidents attributed to the Bell network" if r["scope"] == "isp" else "all incidents"
    lines = [
        "# Network incident report",
        "",
        f"- **Period:** {r['period']['start']} to {r['period']['end']}",
        f"- **Generated:** {r['generated_at']} by NetWatch {r['version']}",
        f"- **Connection:** Bell fibre, Home Hub at {r['gateway'] or 'unknown'}; measurements "
        "taken from a wired server on the home network",
        f"- **Scope:** {scope}",
        "",
        "## Summary",
        "",
        f"- Incidents in this period: **{s['incidents']}** (attributed to the Bell network: "
        f"**{s['isp_incidents']}**)",
        f"- Internet downtime attributed to the Bell network: **{s['isp_downtime']}** "
        f"(availability {s['isp_availability_pct']} %)",
        f"- Longest incident: {s['longest']}",
    ]
    for p in r["patterns"]:
        lines.append(f"- Pattern: {p['title']}")
    if r["ruled_out"]:
        lines += ["", "## Ruled out", ""] + [f"- {x}" for x in r["ruled_out"]]
    if r["incidents"]:
        lines += [
            "",
            "## Incidents",
            "",
            "| Start | End | Duration | Affected | Most likely cause | Confidence |",
            "|---|---|---|---|---|---|",
        ]
        for i in r["incidents"]:
            affected = ", ".join(f["name"] for f in i["failing"]) or "—"
            lines.append(
                f"| {i['start']} | {i['end']} | {i['duration']} | {affected} | {i['cause']} | "
                f"{i['confidence']} % |"
            )
        lines += ["", "## Evidence per incident", ""]
        for i in r["incidents"]:
            lines += [f"### {i['start']} ({i['duration']})", ""]
            if i["causes"]:
                top = i["causes"][0]
                lines.append(f"**{top['title']}** ({top['confidence']} %)")
                lines += [f"- {e}" for e in top["evidence"]]
            if i["healthy"]:
                lines.append(
                    "- Still working during the incident: "
                    + ", ".join(h["name"] for h in i["healthy"])
                )
            for p in i["path_hops"]:
                lines += [
                    "",
                    f"Path test to {p['host']} at {p['ts']}:",
                    "",
                    "| Hop | Host | Loss | Avg |",
                    "|---|---|---|---|",
                ]
                for h in p["hops"]:
                    lines.append(
                        f"| {h['hop']} | {h['host'] or '???'} | {h['loss_pct']} % | "
                        f"{_ms(h['avg'])} |"
                    )
            lines.append("")
    if r["targets"]:
        lines += [
            "## Measurements for the whole period",
            "",
            "| Target | Address | Layer | Probes | Loss | Median | 95th pct |",
            "|---|---|---|---|---|---|---|",
        ]
        for t in r["targets"]:
            lines.append(
                f"| {t['name']} | {t['host']} | {t['layer_label']} | {t['probes']} | "
                f"{t['loss_pct']} % | {_ms(t['median_ms'])} | {_ms(t['p95_ms'])} |"
            )
    ln = r.get("line") or {}
    if ln:
        lines += ["", "## Bell line (from the Home Hub) and latency under load", ""]
        if ln.get("hub_restarts") is not None:
            lines.append(f"- Home Hub restarts: {ln['hub_restarts']}")
            lines.append(f"- Link down periods reported by the Hub: {ln['wan_down']}")
            lines.append(f"- Internet session reconnects: {ln['ppp_reconnects']}")
        if ln.get("rx_dbm_min") is not None:
            lines.append(
                f"- Fibre receive level: {ln['rx_dbm_avg']} dBm average, "
                f"{ln['rx_dbm_min']} dBm lowest"
            )
        for e in ln.get("events", [])[:20]:
            lines.append(f"  - {e['when']}: {e['title']}")
        if ln.get("bufferbloat"):
            b = ln["bufferbloat"]
            lines.append(
                f"- Latency under load: grade {b['grade']} (idle {_ms(b['idle_ms'])}, "
                f"downloading {_ms(b['down_ms'])}, uploading {_ms(b['up_ms'])}; {b['when']})"
            )
    if r["speeds"]:
        lines += [
            "",
            "## Speed tests (iperf3)",
            "",
            "| Type | Direction | Tests | Average | Min | Max |",
            "|---|---|---|---|---|---|",
        ]
        for sp in r["speeds"]:
            lines.append(
                f"| {sp['kind'].upper()} | {sp['direction']} | {sp['tests']} | "
                f"{_bps(sp['avg_bps'])} | {_bps(sp['min_bps'])} | {_bps(sp['max_bps'])} |"
            )
    lines.append("")
    return "\n".join(lines)


def _line_evidence(diag, hub_store, bufferbloat_store, start_ms, end_ms, local) -> dict:
    """What the Home Hub itself reported, and the latest bufferbloat grade."""
    out: dict = {}
    if hub_store is not None:
        hist = hub_store.history(start_ms, end_ms, limit=200_000)
        if hist:
            rx = [h["rx_dbm"] for h in hist if h["rx_dbm"] is not None]
            hub_ev = diag.events.list(start_ms, end_ms, kinds=["hub"], limit=2000)
            by_type: dict[str, int] = {}
            for e in hub_ev:
                t = e["details"].get("type")
                by_type[t] = by_type.get(t, 0) + 1
            out.update(
                hub_restarts=by_type.get("hub_restart", 0),
                wan_down=by_type.get("wan_down", 0),
                ppp_reconnects=by_type.get("ppp_reconnect", 0),
                rx_dbm_min=min(rx) if rx else None,
                rx_dbm_avg=round(sum(rx) / len(rx), 2) if rx else None,
                events=[
                    {"when": local(e["start_ms"]), "title": e["title"]}
                    for e in sorted(hub_ev, key=lambda e: e["start_ms"])
                ],
            )
    if bufferbloat_store is not None:
        tests = [
            t
            for t in bufferbloat_store.list(500)
            if t["status"] == "ok" and start_ms <= t["ts_ms"] < end_ms
        ]
        if tests:
            b = tests[0]
            out["bufferbloat"] = {k: b[k] for k in ("grade", "idle_ms", "down_ms", "up_ms")} | {
                "when": local(b["ts_ms"]),
                "tests": len(tests),
            }
    return out


def report_filename(r: dict) -> str:
    start = datetime.fromtimestamp(r["period"]["start_ms"] / 1000).strftime("%Y%m%d")
    end = datetime.fromtimestamp(r["period"]["end_ms"] / 1000).strftime("%Y%m%d")
    return f"netwatch-incidents-{start}-{end}.md"
