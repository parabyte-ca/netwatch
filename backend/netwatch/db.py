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

Index("ix_samples_ts", samples.c.ts_ms)
Index("ix_rollup_1m_ts", rollup_1m.c.bucket_ts)


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
