"""Runtime settings, read from NETWATCH_* environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETWATCH_", extra="ignore")

    bind: str = "0.0.0.0"
    port: int = Field(default=8085, ge=1, le=65535)
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
    seed_defaults: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "netwatch.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


@lru_cache
def get_settings() -> Settings:
    return Settings()
