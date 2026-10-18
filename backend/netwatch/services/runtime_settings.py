"""Settings editable in the web UI.

Saved values live in the database (kv ``settings_overrides``) and take precedence over the
environment (.env). They are applied to the shared Settings object at startup; when saved from
the UI they are applied straight away where the running services can pick them up ("live"),
otherwise they take effect after a restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from annotated_types import Ge, Gt, Le
from pydantic import TypeAdapter, ValidationError

from ..config import Settings

OVERRIDES_KEY = "settings_overrides"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True)
class Editable:
    name: str
    group: str
    label: str
    help: str
    live: bool
    unit: str = ""


FIELDS: tuple[Editable, ...] = (
    # Detection
    Editable("down_after", "detection", "Failures before down",
             "Consecutive failed probes before a target is declared down.", True, "probes"),
    Editable("up_after", "detection", "Successes before recovered",
             "Consecutive successful probes before it is declared up again.", True, "probes"),
    Editable("degraded_enter_pct", "detection", "Packet-loss start",
             "Loss over the sliding window that opens a packet-loss event.", True, "%"),
    Editable("degraded_exit_pct", "detection", "Packet-loss end",
             "Loss at or below which the packet-loss event closes.", True, "%"),
    # Path tests
    Editable("path_test_interval_min", "path_tests", "Scheduled path tests",
             "Minutes between mtr runs to public targets (0 = off).", False, "min"),
    Editable("path_test_cycles", "path_tests", "Probes per hop",
             "mtr report cycles per run.", True),
    Editable("path_test_on_outage", "path_tests", "Path test when an outage starts",
             "Run mtr automatically when a target goes down.", True),
    Editable("path_test_cooldown_s", "path_tests", "Outage path-test cooldown",
             "Minimum time between automatic path tests to the same host.", True, "s"),
    # Discovery
    Editable("discovery_passive", "discovery", "Passive discovery",
             "Listen for ARP, DHCP, mDNS and SSDP to build the device inventory.", False),
    Editable("discovery_interface", "discovery", "Capture interface",
             "Empty = the default-route interface.", False),
    Editable("discovery_subnets", "discovery", "Subnets to scan",
             "Comma-separated, private ranges only, at most /22 each. Empty = the interface's "
             "own subnet.", True),
    Editable("discovery_scan_interval_min", "discovery", "Scan interval",
             "Minutes between scheduled active scans (switch scanning on or off on the "
             "Devices page).", True, "min"),
    Editable("discovery_scan_rate_pps", "discovery", "ARP sweep rate",
             "Packets per second during a scan.", True, "pkt/s"),
    Editable("arp_storm_pps", "discovery", "ARP storm threshold",
             "ARP rate (10 s average) that opens an ARP-storm event.", True, "pkt/s"),
    Editable("dns_check_interval_min", "discovery", "DNS consistency check",
             "Minutes between resolver comparisons (0 = off).", False, "min"),
    # Bufferbloat
    Editable("bufferbloat_nightly", "path_tests", "Nightly bufferbloat test",
             "Saturates the internet line for about 30 s each night (20 min after the update "
             "window opens) to grade latency under load.", True),
    # Host NIC
    Editable("nic_interfaces", "nic", "Interfaces to watch",
             "Comma-separated. Empty = default-route interface plus its physical ports.", True),
    Editable("nic_poll_s", "nic", "Poll interval",
             "How often the NIC counters are read.", True, "s"),
    # Retention
    Editable("raw_retention_days", "retention", "Raw samples",
             "Per-probe samples (used for graphs of 6 hours or less).", True, "days"),
    Editable("rollup_retention_days", "retention", "1-minute rollups",
             "Long-range graphs and reports.", True, "days"),
    Editable("event_retention_days", "retention", "Timeline events", "", True, "days"),
    Editable("path_test_retention_days", "retention", "Path tests", "", True, "days"),
    Editable("throughput_retention_days", "retention", "Speed tests", "", True, "days"),
    Editable("device_retention_days", "retention", "Unseen devices",
             "Named or monitored devices are always kept.", True, "days"),
    Editable("traffic_retention_days", "retention", "Per-device traffic (1-minute)",
             "Bandwidth per device from the Decos; older traffic is kept as hourly totals "
             "for as long as the 1-minute rollups.", True, "days"),
    # Security
    Editable("session_days", "security", "Session lifetime",
             "How long a sign-in lasts (applies to new sign-ins).", True, "days"),
    Editable("login_max_failures", "security", "Failed sign-ins before lockout",
             "Per client address, within 5 minutes.", True),
    Editable("login_lockout_s", "security", "Lockout duration", "", True, "s"),
    Editable("cookie_secure", "security", "Secure cookie (HTTPS only)",
             "Only switch on when NetWatch is served over HTTPS, or you won't be able to sign "
             "in.", True),
    Editable("metrics_enabled", "security", "Prometheus /metrics", "", False),
    Editable("metrics_token", "security", "Metrics bearer token",
             "Lets Prometheus scrape /metrics without a session. Empty = session required.", True),
    Editable("log_level", "security", "Log level", "", True),
)  # fmt: skip

BY_NAME = {f.name: f for f in FIELDS}
GROUPS = {
    "detection": "Outage detection",
    "path_tests": "Path and load tests",
    "discovery": "Discovery",
    "nic": "TrueNAS network interface",
    "retention": "Data retention",
    "security": "Security and logging",
}


def _adapter(name: str) -> TypeAdapter:
    if name == "log_level":
        return TypeAdapter(Literal[LOG_LEVELS])  # type: ignore[valid-type]
    info = Settings.model_fields[name]
    return TypeAdapter(Annotated[info.annotation, info])


def _kind(value: Any, name: str) -> str:
    if name == "log_level":
        return "choice"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "str"


def _bounds(name: str) -> dict:
    out: dict = {}
    if name not in Settings.model_fields:
        return out
    for m in Settings.model_fields[name].metadata:
        if isinstance(m, Ge):
            out["min"] = m.ge
        elif isinstance(m, Gt):
            out["min"] = m.gt
        elif isinstance(m, Le):
            out["max"] = m.le
    return out


class SettingsError(ValueError):
    pass


def validate_changes(settings: Settings, changes: dict[str, Any], base: Settings) -> dict:
    """Validate {name: value | None}; None means back to the .env/default value.
    Returns {name: value-to-apply} (None kept to signal reset) or raises SettingsError."""
    out: dict[str, Any] = {}
    errors: list[str] = []
    for name, value in changes.items():
        if name not in BY_NAME:
            errors.append(f"{name}: not editable")
            continue
        if value is None:
            out[name] = None
            continue
        if isinstance(value, str):
            value = value.strip()
            if name == "log_level":
                value = value.upper()
        try:
            out[name] = _adapter(name).validate_python(value)
        except ValidationError as exc:
            errors.append(f"{BY_NAME[name].label}: {exc.errors()[0]['msg']}")
    if errors:
        raise SettingsError("; ".join(errors))

    def eff(n: str):
        if n in out:
            return getattr(base, n) if out[n] is None else out[n]
        return getattr(settings, n)

    if eff("degraded_exit_pct") >= eff("degraded_enter_pct"):
        errors.append("Packet-loss end must be lower than packet-loss start")
    if eff("raw_retention_days") > eff("rollup_retention_days"):
        errors.append("Raw samples cannot be kept longer than the 1-minute rollups")
    if out.get("discovery_subnets"):
        from .discovery.capture import scan_networks

        _nets, notes = scan_networks(
            [s.strip() for s in out["discovery_subnets"].split(",") if s.strip()], None
        )
        bad = [n for n in notes if "skipped" in n or "not an IPv4" in n]
        if bad:
            errors.append("; ".join(bad))
    if errors:
        raise SettingsError("; ".join(errors))
    return out


def apply_values(settings: Settings, values: dict[str, Any], base: Settings) -> None:
    for name, value in values.items():
        setattr(settings, name, getattr(base, name) if value is None else value)


def merge_overrides(current: dict, values: dict) -> dict:
    merged = dict(current)
    for name, value in values.items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
    return merged


def load_overrides(raw: Any) -> dict:
    """Saved overrides that still validate (a renamed or tightened field is dropped)."""
    if not isinstance(raw, dict):
        return {}
    good = {}
    for name, value in raw.items():
        if name not in BY_NAME:
            continue
        try:
            good[name] = _adapter(name).validate_python(value)
        except ValidationError:
            logging.getLogger(__name__).warning(
                "ignoring invalid saved setting", extra={"setting": name}
            )
    return good


def describe(settings: Settings, base: Settings, overrides: dict) -> list[dict]:
    out = []
    for f in FIELDS:
        value = getattr(settings, f.name)
        item = {
            "name": f.name,
            "group": f.group,
            "group_label": GROUPS[f.group],
            "label": f.label,
            "help": f.help,
            "unit": f.unit,
            "live": f.live,
            "type": _kind(value, f.name),
            "value": value,
            "default": getattr(base, f.name),
            "overridden": f.name in overrides,
            **_bounds(f.name),
        }
        if f.name == "log_level":
            item["choices"] = list(LOG_LEVELS)
        if f.name == "metrics_token":
            item["secret"] = True
            item["value"] = "••••••••" if value else ""
            item["default"] = "••••••••" if item["default"] else ""
        out.append(item)
    return out
