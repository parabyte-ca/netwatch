"""API request/response models and target validation rules."""

from __future__ import annotations

import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal["icmp", "dns", "tcp", "http"]
Group = Literal["gateway", "mesh", "public", "dns", "device", "custom"]

_LABEL = re.compile(r"^(?!-)[A-Za-z0-9_-]{1,63}(?<!-)$")
MIN_INTERVAL = {"icmp": 1.0, "dns": 5.0, "tcp": 5.0, "http": 10.0}
DEFAULT_TIMEOUT = {"icmp": 2.0, "dns": 3.0, "tcp": 5.0, "http": 10.0}


def valid_host(value: str) -> str:
    """Accept an IP literal or an RFC 1123 hostname. Rejects anything shell- or flag-like."""
    value = value.strip().rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if not value or len(value) > 253 or not all(_LABEL.match(p) for p in value.split(".")):
        raise ValueError("must be an IP address or a valid hostname")
    return value


class TargetIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    kind: Kind
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    url: str | None = Field(default=None, max_length=2048)
    dns_query: str | None = Field(default=None, max_length=253)
    dns_rdtype: Literal["A", "AAAA"] | None = None
    expect_status: int | None = Field(default=None, ge=100, le=599)
    interval_s: float = Field(ge=1.0, le=3600.0)
    timeout_s: float | None = Field(default=None, ge=0.2, le=60.0)
    enabled: bool = True
    group: Group = "custom"

    @field_validator("host")
    @classmethod
    def _host(cls, v: str | None) -> str | None:
        return valid_host(v) if v else None

    @field_validator("dns_query")
    @classmethod
    def _query(cls, v: str | None) -> str | None:
        return valid_host(v) if v else None

    @model_validator(mode="after")
    def _per_kind(self) -> TargetIn:
        k = self.kind
        if self.interval_s < MIN_INTERVAL[k]:
            raise ValueError(f"{k} interval must be at least {MIN_INTERVAL[k]:g} s")
        if k == "http":
            if not self.url:
                raise ValueError("url is required for http targets")
            parts = urlsplit(self.url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError("url must be an absolute http:// or https:// URL")
            self.host = valid_host(parts.hostname)
            self.port = self.dns_query = self.dns_rdtype = None
        else:
            if not self.host:
                raise ValueError("host is required")
            self.url = self.expect_status = None
        if k == "tcp" and self.port is None:
            raise ValueError("port is required for tcp targets")
        if k == "dns":
            try:
                ipaddress.ip_address(self.host)
            except ValueError as exc:
                raise ValueError("dns targets need the resolver's IP address as host") from exc
            self.dns_query = self.dns_query or "www.google.com"
            self.dns_rdtype = self.dns_rdtype or "A"
        else:
            self.dns_query = self.dns_rdtype = None
        if k == "icmp":
            self.port = None
        if self.timeout_s is None:
            self.timeout_s = min(DEFAULT_TIMEOUT[k], self.interval_s)
        if self.timeout_s > self.interval_s:
            raise ValueError("timeout must not exceed the interval")
        return self


class TargetOut(BaseModel):
    id: int
    name: str
    kind: Kind
    host: str
    port: int | None
    url: str | None
    dns_query: str | None
    dns_rdtype: str | None
    expect_status: int | None
    interval_s: float
    timeout_s: float
    enabled: bool
    group: str
    auto: bool
    created_at: float
    updated_at: float


class ProbeResultOut(BaseModel):
    target_id: int
    ts_ms: int
    ok: bool
    rtt_ms: float | None
    error: str | None
