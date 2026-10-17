"""SQLite storage: schema, engine with WAL pragmas, and Alembic migration runner."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    Boolean,
    Column,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)

log = logging.getLogger(__name__)

metadata = MetaData()

targets = Table(
    "targets",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(64), nullable=False),
    Column("kind", String(8), nullable=False),  # icmp | dns | tcp | http
    Column("host", String(253), nullable=False),
    Column("port", Integer),
    Column("url", String(2048)),
    Column("dns_query", String(253)),
    Column("dns_rdtype", String(8)),
    Column("expect_status", Integer),
    Column("interval_s", Float, nullable=False),
    Column("timeout_s", Float, nullable=False),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("group", String(16), nullable=False, default="custom"),
    Column("auto", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

# One row per probe. WITHOUT ROWID keeps the (target, ts) key as the clustered index,
# which halves storage versus a rowid table plus a secondary index.
samples = Table(
    "samples",
    metadata,
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="CASCADE"), primary_key=True),
    Column("ts_ms", Integer, primary_key=True),
    Column("rtt_ms", Float),
    Column("ok", Boolean, nullable=False),
    Column("error", String(64)),
    sqlite_with_rowid=False,
)

rollup_1m = Table(
    "rollup_1m",
    metadata,
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="CASCADE"), primary_key=True),
    Column("bucket_ts", Integer, primary_key=True),  # epoch seconds, minute aligned
    Column("count", Integer, nullable=False),
    Column("lost", Integer, nullable=False),
    Column("min", Float),
    Column("p5", Float),
    Column("p25", Float),
    Column("median", Float),
    Column("p75", Float),
    Column("p95", Float),
    Column("max", Float),
    Column("avg", Float),
    Column("jitter", Float),
    sqlite_with_rowid=False,
)

kv = Table(
    "kv",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

# Server-side sessions. Only a SHA-256 of the cookie token is stored.
sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_ms", Integer, nullable=False),
    Column("last_seen_ms", Integer, nullable=False),
    Column("expires_ms", Integer, nullable=False),
    Column("ip", String(64)),
    Column("user_agent", String(200)),
)

# Timeline: outages, degradations, NIC link events, monitor lifecycle. end_ms is NULL while open.
events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("severity", String(8), nullable=False),
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="SET NULL")),
    Column("iface", String(32)),
    Column("start_ms", Integer, nullable=False),
    Column("end_ms", Integer),
    Column("title", String(200), nullable=False),
    Column("details", Text, nullable=False, default="{}"),
)

# Host NIC counters, one row per interface per minute (deltas, plus the link state snapshot).
nic_stats = Table(
    "nic_stats",
    metadata,
    Column("iface", String(32), primary_key=True),
    Column("bucket_ts", Integer, primary_key=True),
    Column("operstate", String(16)),
    Column("speed", Integer),
    Column("duplex", String(8)),
    Column("carrier_changes", Integer, nullable=False, default=0),
    Column("rx_bytes", Integer, nullable=False, default=0),
    Column("tx_bytes", Integer, nullable=False, default=0),
    Column("rx_packets", Integer, nullable=False, default=0),
    Column("tx_packets", Integer, nullable=False, default=0),
    Column("rx_errors", Integer, nullable=False, default=0),
    Column("tx_errors", Integer, nullable=False, default=0),
    Column("rx_dropped", Integer, nullable=False, default=0),
    Column("tx_dropped", Integer, nullable=False, default=0),
    Column("rx_crc_errors", Integer, nullable=False, default=0),
    sqlite_with_rowid=False,
)

path_tests = Table(
    "path_tests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("ts_ms", Integer, nullable=False),
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="SET NULL")),
    Column("event_id", Integer, ForeignKey("events.id", ondelete="SET NULL")),
    Column("host", String(253), nullable=False),
    Column("trigger", String(16), nullable=False),  # scheduled | outage | manual
    Column("status", String(8), nullable=False),  # ok | error
    Column("duration_ms", Integer),
    Column("hop_count", Integer),
    Column("reached", Boolean),
    Column("first_loss_hop", Integer),
    Column("path_changed", Boolean),
    Column("hops", Text, nullable=False, default="[]"),
    Column("error", String(200)),
)

iperf_servers = Table(
    "iperf_servers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(64), nullable=False),
    Column("host", String(253), nullable=False),
    Column("port", Integer, nullable=False),
    Column("kind", String(8), nullable=False),  # lan | wan
    Column("duration_s", Integer, nullable=False),
    Column("parallel", Integer, nullable=False),
    Column("interval_min", Integer, nullable=False),  # 0 = manual only
    Column("enabled", Boolean, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

throughput_tests = Table(
    "throughput_tests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("ts_ms", Integer, nullable=False),
    Column("server_id", Integer, ForeignKey("iperf_servers.id", ondelete="SET NULL")),
    Column("host", String(253), nullable=False),
    Column("port", Integer, nullable=False),
    Column("kind", String(8), nullable=False),
    Column("direction", String(8), nullable=False),  # download | upload
    Column("trigger", String(16), nullable=False),
    Column("status", String(8), nullable=False),
    Column("duration_s", Float),
    Column("bits_per_second", Float),
    Column("retransmits", Integer),
    Column("error", String(200)),
    Column("details", Text, nullable=False, default="{}"),
)

Index("ix_samples_ts", samples.c.ts_ms)
Index("ix_rollup_1m_ts", rollup_1m.c.bucket_ts)
Index("ix_sessions_user", sessions.c.user_id)
Index("ix_events_start", events.c.start_ms)
Index("ix_events_target", events.c.target_id)
Index("ix_nic_stats_ts", nic_stats.c.bucket_ts)
Index("ix_path_tests_ts", path_tests.c.ts_ms)
Index("ix_path_tests_host", path_tests.c.host)
Index("ix_throughput_tests_ts", throughput_tests.c.ts_ms)


def _set_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    # auto_vacuum only takes effect on a fresh database; harmless afterwards.
    cur.execute("PRAGMA auto_vacuum=INCREMENTAL")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


def make_engine(db_path: Path | str) -> Engine:
    url = "sqlite://" if str(db_path) == ":memory:" else f"sqlite:///{db_path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _set_pragmas)
    return engine


def run_migrations(engine: Engine) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    log.info("database schema at head")
