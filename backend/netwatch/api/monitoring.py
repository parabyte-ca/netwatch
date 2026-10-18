"""Timeline events, host NIC telemetry, path tests (mtr) and throughput tests (iperf3)."""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..schemas import valid_host
from ..services.events import KINDS, LOSS, OUTAGE

router = APIRouter(prefix="/api")

EVIDENCE_PAD_MS = 5 * 60_000


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── events ────────────────────────────────────────────────────────────────────


@router.get("/events")
async def list_events(
    request: Request,
    start: int | None = Query(default=None, description="epoch seconds"),
    end: int | None = Query(default=None, description="epoch seconds"),
    kind: list[str] = Query(default=[]),
    target_id: int | None = None,
    open_only: bool = False,
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[dict]:
    end_ms = (end or int(time.time()) + 1) * 1000
    start_ms = (start or int(time.time()) - 7 * 86400) * 1000
    bad = [k for k in kind if k not in KINDS]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown kind: {', '.join(bad)}")
    return await asyncio.to_thread(
        request.app.state.events.list,
        start_ms,
        end_ms,
        kinds=kind,
        target_id=target_id,
        open_only=open_only,
        limit=limit,
    )


@router.get("/events/active")
async def active_events(request: Request) -> list[dict]:
    return await asyncio.to_thread(
        request.app.state.events.list, 0, _now_ms() + 1, open_only=True, limit=200
    )


@router.get("/events/{event_id}")
async def event_detail(event_id: int, request: Request) -> dict:
    """The event plus the evidence around it: what else failed, NIC events and path tests."""
    st = request.app.state
    ev = await asyncio.to_thread(st.events.get, event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    win_start = ev["start_ms"] - EVIDENCE_PAD_MS
    win_end = (ev["end_ms"] or _now_ms()) + EVIDENCE_PAD_MS
    related = await asyncio.to_thread(st.events.list, win_start, win_end, limit=500)
    related = [e for e in related if e["id"] != event_id]
    tests = await asyncio.to_thread(
        st.path_store.list, start_ms=win_start, end_ms=win_end, limit=20
    )
    by_event = await asyncio.to_thread(st.path_store.list, event_id=event_id, limit=20)
    seen = {t["id"] for t in by_event}
    target = (
        await asyncio.to_thread(st.store.get_target, ev["target_id"]) if ev["target_id"] else None
    )
    return {
        "event": ev,
        "target": target,
        "window": {"start_ms": win_start, "end_ms": win_end},
        "concurrent_failures": [e for e in related if e["kind"] in (OUTAGE, LOSS)],
        "nic_events": [e for e in related if e["kind"].startswith("nic_")],
        "other_events": [
            e
            for e in related
            if e["kind"] not in (OUTAGE, LOSS) and not e["kind"].startswith("nic_")
        ],
        "path_tests": by_event + [t for t in tests if t["id"] not in seen],
    }


# ── host NIC ──────────────────────────────────────────────────────────────────


@router.get("/nic")
async def nic_status(request: Request) -> dict:
    nic = request.app.state.nic
    return {"poll_s": nic.poll_s, "interfaces": nic.status()}


@router.get("/nic/{iface}/history")
async def nic_history(
    iface: str,
    request: Request,
    start: int | None = None,
    end: int | None = None,
) -> list[dict]:
    if iface not in request.app.state.nic.watched:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interface not monitored")
    end_s = end or int(time.time()) + 60
    start_s = start or end_s - 86400
    return await asyncio.to_thread(request.app.state.nic_store.history, iface, start_s, end_s)


# ── path tests ────────────────────────────────────────────────────────────────


class PathTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: int | None = None
    host: str | None = Field(default=None, max_length=253)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str | None) -> str | None:
        return valid_host(v) if v else None

    @model_validator(mode="after")
    def _one(self) -> PathTestIn:
        if (self.target_id is None) == (self.host is None):
            raise ValueError("give either target_id or host")
        return self


@router.get("/path-tests")
async def list_path_tests(
    request: Request,
    target_id: int | None = None,
    event_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    return await asyncio.to_thread(
        request.app.state.path_store.list, target_id=target_id, event_id=event_id, limit=limit
    )


@router.post("/path-tests", status_code=status.HTTP_201_CREATED)
async def run_path_test(body: PathTestIn, request: Request) -> dict:
    """Run mtr now (takes a few seconds) and return the stored result."""
    st = request.app.state
    host, target_id = body.host, body.target_id
    if target_id is not None:
        target = await asyncio.to_thread(st.store.get_target, target_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "target not found")
        host = target["host"]
    return await st.path_tester.run(host, trigger="manual", target_id=target_id)


@router.get("/path-tests/{test_id}")
async def get_path_test(test_id: int, request: Request) -> dict:
    t = await asyncio.to_thread(request.app.state.path_store.get, test_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "path test not found")
    return t


# ── iperf3 ────────────────────────────────────────────────────────────────────


class IperfServerIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(max_length=253)
    port: int = Field(default=5201, ge=1, le=65535)
    kind: Literal["lan", "wan"] = "lan"
    duration_s: int = Field(default=10, ge=3, le=60)
    parallel: int = Field(default=1, ge=1, le=8)
    interval_min: int = Field(default=0, ge=0, le=10080)
    enabled: bool = True

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        return valid_host(v)

    @field_validator("interval_min")
    @classmethod
    def _interval(cls, v: int) -> int:
        if 0 < v < 15:
            raise ValueError("scheduled tests need an interval of at least 15 minutes (0 = manual)")
        return v


@router.get("/iperf/servers")
async def list_iperf_servers(request: Request) -> list[dict]:
    return await asyncio.to_thread(request.app.state.throughput_store.list_servers)


@router.post("/iperf/servers", status_code=status.HTTP_201_CREATED)
async def create_iperf_server(body: IperfServerIn, request: Request) -> dict:
    return await asyncio.to_thread(
        request.app.state.throughput_store.create_server, body.model_dump()
    )


@router.put("/iperf/servers/{server_id}")
async def update_iperf_server(server_id: int, body: IperfServerIn, request: Request) -> dict:
    s = await asyncio.to_thread(
        request.app.state.throughput_store.update_server, server_id, body.model_dump()
    )
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    return s


@router.delete("/iperf/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_iperf_server(server_id: int, request: Request) -> Response:
    if not await asyncio.to_thread(request.app.state.throughput_store.delete_server, server_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/iperf/servers/{server_id}/run")
async def run_iperf(server_id: int, request: Request) -> list[dict]:
    """Run download then upload now (about twice the duration)."""
    st = request.app.state
    server = await asyncio.to_thread(st.throughput_store.get_server, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "server not found")
    if st.throughput.busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "another throughput test is running")
    return await st.throughput.run_server(server, trigger="manual")


@router.get("/iperf/tests")
async def list_iperf_tests(
    request: Request,
    server_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return await asyncio.to_thread(
        request.app.state.throughput_store.list_tests, server_id=server_id, limit=limit
    )


# ── bufferbloat ───────────────────────────────────────────────────────────────


@router.get("/bufferbloat")
async def bufferbloat(request: Request) -> dict:
    st = request.app.state
    tests = await asyncio.to_thread(st.bufferbloat_store.list, 60)
    return {
        "running": st.bufferbloat.running,
        "nightly": st.settings.bufferbloat_nightly,
        "tests": tests,
    }


@router.post("/bufferbloat/run", status_code=status.HTTP_202_ACCEPTED)
async def run_bufferbloat(request: Request) -> dict:
    if not request.app.state.bufferbloat.start_now():
        raise HTTPException(status.HTTP_409_CONFLICT, "a bufferbloat test is already running")
    return {"started": True}
