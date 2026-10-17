from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from netwatch.config import Settings
from netwatch.db import make_engine, run_migrations
from netwatch.main import create_app
from netwatch.probes import ProbeResult, TargetSpec
from netwatch.probes.base import now_ms
from netwatch.services.storage import Store


class FakeProber:
    """Deterministic prober: every probe succeeds with 10 ms unless the host is listed as down."""

    def __init__(self) -> None:
        self.down: set[str] = set()
        self.calls: list[int] = []

    async def run(self, t: TargetSpec) -> ProbeResult:
        self.calls.append(t.id)
        if t.host in self.down:
            return ProbeResult(t.id, now_ms(), False, error="timeout")
        return ProbeResult(t.id, now_ms(), True, rtt_ms=10.0)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        static_dir=tmp_path / "static",
        seed_defaults=False,
        log_format="text",
        flush_interval_s=0.05,
    )


@pytest.fixture
def store(tmp_path) -> Store:
    engine = make_engine(tmp_path / "test.db")
    run_migrations(engine)
    yield Store(engine)
    engine.dispose()


@pytest.fixture
def prober() -> FakeProber:
    return FakeProber()


@pytest.fixture
def client(settings, prober):
    app = create_app(settings, prober=prober)
    with TestClient(app) as c:
        yield c


def make_target(store: Store, **over) -> dict:
    values = {
        "name": "t",
        "kind": "icmp",
        "host": "192.0.2.1",
        "interval_s": 1.0,
        "timeout_s": 1.0,
        "enabled": True,
        "group": "custom",
        "auto": False,
    }
    values.update(over)
    return store.create_target(values)
