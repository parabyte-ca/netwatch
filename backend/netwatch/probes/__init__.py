"""Probe dispatch: one coroutine per probe kind, all returning ProbeResult and never raising."""

from __future__ import annotations

import logging

import httpx

from .base import ProbeResult, TargetSpec, now_ms
from .dns import probe_dns
from .http import probe_http
from .icmp import probe_icmp
from .tcp import probe_tcp

log = logging.getLogger(__name__)

KINDS = ("icmp", "dns", "tcp", "http")


class Prober:
    """Runs a single probe for a target. Holds shared resources such as the HTTP client."""

    def __init__(self, *, icmp_privileged: bool = True) -> None:
        self.icmp_privileged = icmp_privileged
        self._http: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                trust_env=False,
                follow_redirects=True,
                max_redirects=5,
                headers={"User-Agent": "NetWatch"},
                limits=httpx.Limits(max_keepalive_connections=0),
            )
        return self._http

    async def run(self, t: TargetSpec) -> ProbeResult:
        try:
            if t.kind == "icmp":
                return await probe_icmp(t, privileged=self.icmp_privileged)
            if t.kind == "dns":
                return await probe_dns(t)
            if t.kind == "tcp":
                return await probe_tcp(t)
            if t.kind == "http":
                return await probe_http(t, self.http)
        except Exception:
            log.exception("probe crashed", extra={"target_id": t.id, "kind": t.kind})
            return ProbeResult(t.id, now_ms(), False, error="internal")
        return ProbeResult(t.id, now_ms(), False, error="unknown_kind")

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


__all__ = ["KINDS", "ProbeResult", "Prober", "TargetSpec"]
