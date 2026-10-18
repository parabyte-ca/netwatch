"""Runtime settings, read from NETWATCH_* environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETWATCH_", extra="ignore")

    bind: str = "0.0.0.0"
    port: int = Field(default=8090, ge=1, le=65535)
    data_dir: Path = Path("/data")
    static_dir: Path = Path("/app/static")

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    access_log: bool = False

    raw_retention_days: int = Field(default=7, ge=1, le=90)
    rollup_retention_days: int = Field(default=90, ge=7, le=730)

    icmp_privileged: bool = True
    max_concurrent_probes: int = Field(default=64, ge=1, le=512)
    max_targets: int = Field(default=200, ge=1, le=2000)
    flush_interval_s: float = Field(default=2.0, gt=0, le=30)

    metrics_enabled: bool = False
    # Bearer token Prometheus uses for /metrics (every other path requires a login session).
    metrics_token: str = ""
    seed_defaults: bool = True

    # Authentication.
    session_days: int = Field(default=30, ge=1, le=365)
    cookie_secure: bool = False  # set true when served over HTTPS (reverse proxy)
    login_max_failures: int = Field(default=5, ge=3, le=50)
    login_lockout_s: int = Field(default=300, ge=30, le=86400)

    # In-app updates. upgrade_dir is shared with the host-side upgrade agent; empty disables.
    upgrade_dir: Path | None = Path("/upgrade")
    # Where the agent reports back (mounted read-only into the container).
    upgrade_status_dir: Path | None = Path("/upgrade-status")
    update_check: bool = True
    update_repo: str = Field(default="parabyte-ca/netwatch", pattern=r"^[\w.-]+/[\w.-]+$")
    # Defaults for the automatic-update policy (editable in the UI afterwards).
    auto_update: Literal["off", "notify", "patch", "minor"] = "patch"
    auto_update_window: str = Field(default="03:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    tz: str = Field(default="America/Toronto", validation_alias="TZ")

    # Outage detection (hysteresis): consecutive failures to declare down, consecutive successes
    # to recover, and loss thresholds to enter/leave the degraded state over a sliding window.
    down_after: int = Field(default=3, ge=1, le=100)
    up_after: int = Field(default=3, ge=1, le=100)
    degraded_enter_pct: float = Field(default=5.0, gt=0, le=100)
    degraded_exit_pct: float = Field(default=1.0, ge=0, le=100)
    event_retention_days: int = Field(default=365, ge=7, le=3650)

    # Host NIC telemetry. Empty = the default-route interface plus its physical members.
    nic_interfaces: str = ""
    # Host names the web UI may be reached by (comma-separated). Empty: any IP address or
    # localhost. Requests for other names are refused, which defeats DNS rebinding.
    allowed_hosts: str = ""
    nic_poll_s: float = Field(default=2.0, ge=0.5, le=60)
    sysfs_root: Path = Path("/sys")
    proc_root: Path = Path("/proc")

    # Path tests (mtr). Scheduled runs cover the "public" targets; 0 disables the schedule.
    path_test_interval_min: int = Field(default=15, ge=0, le=1440)
    path_test_cycles: int = Field(default=10, ge=1, le=100)
    path_test_on_outage: bool = True
    path_test_cooldown_s: int = Field(default=300, ge=0, le=86400)
    path_test_retention_days: int = Field(default=90, ge=1, le=730)

    # Throughput tests (iperf3).
    throughput_retention_days: int = Field(default=365, ge=7, le=3650)

    # Discovery. Passive capture (ARP/DHCP/mDNS/SSDP) and scheduled active scanning (ARP sweep,
    # DHCP server probe, SSDP/mDNS queries, reverse DNS) are both on by default; active scanning
    # can be switched off in the UI (this is only the initial value). Subnets default to the
    # capture interface's own network.
    discovery_passive: bool = True
    discovery_active: bool = True
    discovery_interface: str = ""
    discovery_subnets: str = ""
    discovery_scan_interval_min: int = Field(default=60, ge=15, le=10080)
    discovery_scan_rate_pps: int = Field(default=50, ge=1, le=500)
    arp_storm_pps: int = Field(default=100, ge=10, le=100000)
    dns_check_interval_min: int = Field(default=30, ge=0, le=1440)
    device_retention_days: int = Field(default=365, ge=7, le=3650)

    @property
    def discovery_subnet_list(self) -> list[str]:
        return [i.strip() for i in self.discovery_subnets.split(",") if i.strip()]

    @property
    def nic_interface_list(self) -> list[str]:
        return [i.strip() for i in self.nic_interfaces.split(",") if i.strip()]

    @property
    def allowed_host_set(self) -> set[str]:
        return {h.strip().lower().strip("[]") for h in self.allowed_hosts.split(",") if h.strip()}

    @property
    def db_path(self) -> Path:
        return self.data_dir / "netwatch.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


@lru_cache
def get_settings() -> Settings:
    return Settings()
