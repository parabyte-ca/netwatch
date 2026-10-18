"""Network health score (0-100) and the weekly health report.

The score is a weighted average of a few components, each 0-100. A component without data
(no Home Hub, no bufferbloat test yet, no exposure scan) is left out and the others are
re-weighted, so the score never punishes a feature that isn't set up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, delete, desc, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import health_reports
from .diagnosis import INTERNET, ISP, PUBLIC_DNS

log = logging.getLogger(__name__)

WEEK_MS = 7 * 86_400_000
LAN_LAYERS = {"host", "lan", "gateway", "gateway_dns", "mesh"}
INTERNET_TARGET_LAYERS = {ISP, INTERNET, PUBLIC_DNS}
BLOAT_SCORE = {"A+": 100, "A": 95, "B": 80, "C": 60, "D": 35, "F": 10}


def _clamp(v: float) -> int:
    return round(max(0.0, min(100.0, v)))


def label(score: int | None) -> str:
    if score is None:
        return "No data"
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Poor"


def _component(key: str, name: str, weight: int, score: float, detail: str) -> dict:
    return {"key": key, "label": name, "weight": weight, "score": _clamp(score), "detail": detail}


def compute(report: dict, exposure: dict | None = None) -> dict:
    """Score a period from its incident report (``build_report``) and the exposure summary."""
    s = report["summary"]
    comps: list[dict] = []

    avail = s["isp_availability_pct"]
    comps.append(
        _component(
            "internet",
            "Internet uptime",
            30,
            100 - (100 - avail) * 100,  # 99.9 % (10 min a week) -> 90, 99 % -> 0
            f"{avail:.2f} % available"
            + (f", down {s['isp_downtime']} in total" if s["isp_downtime_ms"] else ""),
        )
    )

    inet = [t for t in report["targets"] if t["layer"] in INTERNET_TARGET_LAYERS]
    if inet:
        loss = sum(t["loss_pct"] or 0 for t in inet) / len(inet)
        p95 = sorted(t["p95_ms"] for t in inet if t["p95_ms"] is not None)
        p95_mid = p95[len(p95) // 2] if p95 else None
        pen = 20 * loss + (max(0.0, (p95_mid or 0) - 60) / 4)  # -1 per 4 ms above 60 ms
        comps.append(
            _component(
                "quality",
                "Connection quality",
                20,
                100 - pen,
                f"{loss:.2f} % packet loss"
                + (f", 95th-percentile latency {p95_mid:.0f} ms" if p95_mid is not None else ""),
            )
        )

    bloat = (report.get("line") or {}).get("bufferbloat")
    if bloat and bloat.get("grade") in BLOAT_SCORE:
        comps.append(
            _component(
                "load",
                "Latency under load",
                15,
                BLOAT_SCORE[bloat["grade"]],
                f"bufferbloat grade {bloat['grade']}",
            )
        )

    lan = [
        i for i in report["incidents"]
        if i["causes"] and i["causes"][0].get("layer") in LAN_LAYERS
    ]  # fmt: skip
    wifi = sum(len(i.get("wifi_events") or []) for i in report["incidents"])
    comps.append(
        _component(
            "home",
            "Home network",
            15,
            100 - 15 * len(lan) - 3 * wifi,
            f"{len(lan)} incident{'s' if len(lan) != 1 else ''} inside the home"
            if lan
            else "no problems inside the home",
        )
    )

    line = report.get("line") or {}
    if line.get("rx_dbm_min") is not None or line.get("hub_restarts") is not None:
        rx = line.get("rx_dbm_min")
        sc = 100.0
        bits = []
        if rx is not None:
            sc = 100 if rx >= -24 else 40 + (rx + 27) * 20 if rx >= -27 else 20
            bits.append(f"weakest fibre signal {rx:.1f} dBm")
        restarts = line.get("hub_restarts") or 0
        sc -= 15 * restarts
        if restarts:
            bits.append(f"{restarts} Home Hub restart{'s' if restarts != 1 else ''}")
        comps.append(_component("line", "Fibre line", 10, sc, ", ".join(bits) or "steady"))

    if exposure and exposure.get("latest"):
        f = exposure.get("findings") or []
        crit = sum(1 for x in f if x["severity"] == "critical")
        warn = sum(1 for x in f if x["severity"] == "warning")
        comps.append(
            _component(
                "exposure",
                "Exposure",
                10,
                100 - 40 * crit - 15 * warn,
                "nothing risky exposed"
                if not (crit or warn)
                else f"{crit} critical, {warn} warning finding{'s' if crit + warn != 1 else ''}",
            )
        )

    total_w = sum(c["weight"] for c in comps)
    score = _clamp(sum(c["score"] * c["weight"] for c in comps) / total_w) if total_w else None
    return {
        "score": score,
        "label": label(score),
        "components": comps,
        "highlights": _highlights(report, comps),
    }


def _highlights(report: dict, comps: list[dict]) -> list[str]:
    s = report["summary"]
    out = []
    if s["isp_incidents"]:
        n = s["isp_incidents"]
        out.append(
            f"The internet dropped {n} time{'s' if n != 1 else ''} for {s['isp_downtime']} "
            "in total (Bell network)."
        )
    else:
        out.append("No internet outages caused by the Bell network.")
    worst = min(comps, key=lambda c: c["score"]) if comps else None
    if worst and worst["score"] < 75:
        out.append(f"Weakest area: {worst['label'].lower()} ({worst['detail']}).")
    for p in (report.get("patterns") or [])[:1]:
        if p.get("title"):
            out.append(f"Pattern: {p['title']}.")
    return out


class HealthService:
    def __init__(
        self,
        engine: Engine,
        build: Callable[[int, int], dict],
        exposure: Callable[[], dict | None] = lambda: None,
        *,
        tz: Callable[[], str] = lambda: "America/Toronto",
    ) -> None:
        self.engine = engine
        self.build = build  # (start_ms, end_ms) -> incident report
        self.exposure = exposure
        self.tz = tz
        self._task: asyncio.Task | None = None

    def score(self, days: int = 7, now_ms: int | None = None) -> dict:
        now = now_ms or int(time.time() * 1000)
        start = now - days * 86_400_000
        out = compute(self.build(start, now), self.exposure())
        return {**out, "start_ms": start, "end_ms": now, "days": days}

    # ── weekly reports ───────────────────────────────────────────────────────

    def week_bounds(self, week_start: str) -> tuple[int, int]:
        zone = ZoneInfo(self.tz())
        d = datetime.fromisoformat(week_start).replace(tzinfo=zone)
        return int(d.timestamp() * 1000), int((d + timedelta(days=7)).timestamp() * 1000)

    def last_full_week(self, now_ms: int | None = None) -> str:
        zone = ZoneInfo(self.tz())
        now = datetime.fromtimestamp((now_ms or time.time() * 1000) / 1000, zone)
        monday = (now - timedelta(days=now.weekday())).date()
        return (monday - timedelta(days=7)).isoformat()

    def generate(self, week_start: str, now_ms: int | None = None) -> dict:
        start, end = self.week_bounds(week_start)
        report = self.build(start, end)
        health = compute(report, self.exposure())
        prev = self.get((datetime.fromisoformat(week_start) - timedelta(days=7)).date().isoformat())
        details = {
            **health,
            "period": report["period"],
            "summary": report["summary"],
            "speeds": report["speeds"],
            "line": report["line"],
            "patterns": report["patterns"],
            "incidents": [
                {k: i[k] for k in ("id", "start", "duration", "cause", "severity", "start_ms")}
                for i in report["incidents"][:50]
            ],
            "incident_count": len(report["incidents"]),
            "previous_score": prev["score"] if prev else None,
            "markdown": render_week(week_start, health, report, prev),
        }
        now = now_ms or int(time.time() * 1000)
        with self.engine.begin() as conn:
            stmt = sqlite_insert(health_reports).values(
                week_start=week_start,
                created_ms=now,
                score=health["score"],
                details=json.dumps(details),
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["week_start"],
                    set_={
                        "created_ms": now,
                        "score": stmt.excluded.score,
                        "details": stmt.excluded.details,
                    },
                )
            )
        return {"week_start": week_start, "created_ms": now, "score": health["score"], **details}

    def get(self, week_start: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(
                select(health_reports).where(health_reports.c.week_start == week_start)
            ).first()
        if r is None:
            return None
        return {
            "week_start": r.week_start,
            "created_ms": r.created_ms,
            "score": r.score,
            **json.loads(r.details or "{}"),
        }

    def list(self, limit: int = 52) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(health_reports).order_by(desc(health_reports.c.week_start)).limit(limit)
            ).all()
        out = []
        for r in rows:
            d = json.loads(r.details or "{}")
            out.append(
                {
                    "week_start": r.week_start,
                    "created_ms": r.created_ms,
                    "score": r.score,
                    "label": d.get("label"),
                    "previous_score": d.get("previous_score"),
                    "highlights": d.get("highlights", []),
                    "incident_count": d.get("incident_count", 0),
                }
            )
        return out

    def purge(self, keep: int = 104) -> int:
        with self.engine.begin() as conn:
            keep_rows = select(health_reports.c.week_start).order_by(
                desc(health_reports.c.week_start)
            ).limit(keep)  # fmt: skip
            return conn.execute(
                delete(health_reports).where(health_reports.c.week_start.not_in(keep_rows))
            ).rowcount

    def start_schedule(self) -> None:
        """Write last week's report on Monday morning (and catch up after downtime)."""

        async def loop() -> None:
            while True:
                await asyncio.sleep(600)
                try:
                    zone = ZoneInfo(self.tz())
                    now = datetime.now(zone)
                    week = await asyncio.to_thread(self.last_full_week)
                    due = now.hour >= 6 or now.weekday() > 0
                    if due and await asyncio.to_thread(self.get, week) is None:
                        await asyncio.to_thread(self.generate, week)
                        log.info("weekly health report written", extra={"week": week})
                except Exception:
                    log.exception("weekly health report failed")

        self._task = asyncio.create_task(loop(), name="netwatch-health-report")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


def render_week(week_start: str, health: dict, report: dict, prev: dict | None) -> str:
    s = report["summary"]
    score = health["score"]
    trend = ""
    if prev and prev.get("score") is not None and score is not None:
        d = score - prev["score"]
        trend = f" ({'+' if d >= 0 else ''}{d} vs the week before)"
    lines = [
        f"# NetWatch weekly health report: week of {week_start}",
        "",
        f"**Health score: {score if score is not None else '—'}/100, {health['label']}**{trend}",
        "",
        f"Period: {report['period']['start']} to {report['period']['end']}",
        "",
        "## Highlights",
        "",
        *[f"- {h}" for h in health["highlights"]],
        "",
        "## Score breakdown",
        "",
        "| Area | Score | Weight | Detail |",
        "|---|---:|---:|---|",
        *[
            f"| {c['label']} | {c['score']} | {c['weight']} | {c['detail']} |"
            for c in health["components"]
        ],
        "",
        "## Internet",
        "",
        f"- Incidents: {s['incidents']} ({s['isp_incidents']} on the Bell network)",
        f"- Availability: {s['isp_availability_pct']} % (down {s['isp_downtime']})",
        f"- Longest incident: {s['longest']}",
    ]
    for sp in report["speeds"]:
        lines.append(
            f"- Speed ({sp['kind']} {sp['direction']}): average {sp['avg_bps'] / 1e6:.0f} Mb/s "
            f"over {sp['tests']} tests"
        )
    if report["incidents"]:
        lines += ["", "## Incidents", ""]
        lines += [
            f"- {i['start']}: {i['cause']} ({i['duration']})" for i in report["incidents"][:20]
        ]
    return "\n".join(lines) + "\n"
