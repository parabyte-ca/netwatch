from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from netwatch.db import metadata
from netwatch.probes.base import ProbeResult
from netwatch.services.storage import WATERMARK_KEY, backup_database

from .conftest import make_target

T0 = 1_760_000_000  # minute aligned epoch seconds


def _minute(n: int) -> int:
    return (T0 // 60 + n) * 60


def test_migrations_match_metadata(store):
    with store.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []


def test_wal_mode_enabled(store):
    with store.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_kv_roundtrip(store):
    assert store.kv_get("x", 5) == 5
    store.kv_set("x", {"a": 1})
    store.kv_set("x", {"a": 2})
    assert store.kv_get("x") == {"a": 2}


def test_insert_samples_ignores_deleted_targets_and_duplicates(store):
    t = make_target(store)
    rows = [
        ProbeResult(t["id"], 1000, True, 5.0),
        ProbeResult(t["id"], 1000, True, 6.0),
        ProbeResult(999, 1000, True, 5.0),
    ]
    assert store.insert_samples(rows) == 2
    assert store.raw_samples(t["id"], 0, 2000) == [(1000, 5.0, True, None)]


def test_rollup_pending_aggregates_complete_minutes_only(store):
    t = make_target(store)
    m0, m1, m2 = _minute(0), _minute(1), _minute(2)
    store.insert_samples(
        [ProbeResult(t["id"], (m0 + i) * 1000, True, 10.0 + i) for i in range(10)]
        + [ProbeResult(t["id"], (m1 + 1) * 1000, False, error="timeout")]
        + [ProbeResult(t["id"], (m2 + 1) * 1000, True, 1.0)]
    )
    store.kv_set(WATERMARK_KEY, m0)
    # now is 20 s into minute 2: minutes 0 and 1 are complete, minute 2 is not.
    assert store.rollup_pending(m2 + 20, raw_retention_days=7) == 2
    rows = store.rollups(t["id"], m0, m2 + 60)
    assert [r["bucket_ts"] for r in rows] == [m0, m1]
    assert rows[0]["count"] == 10 and rows[0]["lost"] == 0 and rows[0]["min"] == 10.0
    assert rows[1]["count"] == 1 and rows[1]["lost"] == 1 and rows[1]["median"] is None
    assert store.kv_get(WATERMARK_KEY) == m2
    # Re-running is a no-op; advancing time picks up minute 2.
    assert store.rollup_pending(m2 + 20, raw_retention_days=7) == 0
    assert store.rollup_pending(m2 + 80, raw_retention_days=7) == 1


def test_purge_respects_retention(store):
    t = make_target(store)
    now = T0 + 10 * 86400
    store.insert_samples(
        [
            ProbeResult(t["id"], (now - 8 * 86400) * 1000, True, 1.0),
            ProbeResult(t["id"], (now - 1 * 86400) * 1000, True, 1.0),
        ]
    )
    store.kv_set(WATERMARK_KEY, now - 9 * 86400)
    store.rollup_pending(now, raw_retention_days=30)
    removed = store.purge(now, raw_retention_days=7, rollup_retention_days=7)
    assert removed["samples"] == 1
    assert removed["rollups"] == 1
    assert len(store.raw_samples(t["id"], 0, now * 1000)) == 1


def test_delete_target_cascades(store):
    t = make_target(store)
    store.insert_samples([ProbeResult(t["id"], 1000, True, 1.0)])
    assert store.delete_target(t["id"])
    assert store.raw_samples(t["id"], 0, 2000) == []
    assert not store.delete_target(t["id"])


def test_backup_database(store, tmp_path):
    t = make_target(store, name="backup-me")
    store.insert_samples([ProbeResult(t["id"], 1000, True, 1.0)])
    db_path = store.engine.url.database
    dest = backup_database(db_path, tmp_path / "b" / "copy.db")
    import sqlite3

    conn = sqlite3.connect(dest)
    try:
        assert conn.execute("SELECT name FROM targets").fetchall() == [("backup-me",)]
        assert conn.execute("SELECT count(*) FROM samples").fetchone() == (1,)
    finally:
        conn.close()
