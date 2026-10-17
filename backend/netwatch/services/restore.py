"""Database backups from the web UI, and restores applied safely at the next start.

A restore never swaps the database under a running app: the uploaded (or chosen) backup is
validated, parked as ``restore-pending.db``, and NetWatch restarts. On start, before the
database is opened, the current database is copied to ``backups/pre-restore-*.db`` and the
pending file takes its place; migrations then bring an older backup up to date.
"""

from __future__ import annotations

import gzip
import logging
import re
import shutil
import sqlite3
import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from .. import __version__
from .storage import backup_database

log = logging.getLogger(__name__)

PENDING_NAME = "restore-pending.db"
BACKUP_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}\.db$")
SQLITE_MAGIC = b"SQLite format 3\x00"
GZIP_MAGIC = b"\x1f\x8b"


class RestoreError(ValueError):
    pass


def known_revisions() -> set[str]:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "migrations"))
    return {r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()}


def validate_backup(path: Path) -> dict:
    """Check the file is an intact NetWatch database this version can open."""
    with path.open("rb") as f:
        if f.read(16) != SQLITE_MAGIC:
            raise RestoreError("not a SQLite database")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise RestoreError(f"cannot open database: {exc}") from exc
    try:
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RestoreError(f"database is damaged: {check}")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = {"alembic_version", "targets", "users"} - tables
        if missing:
            raise RestoreError(
                "not a NetWatch database (missing " + ", ".join(sorted(missing)) + ")"
            )
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        rev = row[0] if row else None
        if rev not in known_revisions():
            raise RestoreError("backup is from a newer NetWatch version; update NetWatch first")
        targets = conn.execute("SELECT count(*) FROM targets").fetchone()[0]
        users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    except sqlite3.Error as exc:
        raise RestoreError(f"cannot read database: {exc}") from exc
    finally:
        conn.close()
    return {"schema": rev, "targets": targets, "users": users, "bytes": path.stat().st_size}


def unpack_upload(src: Path, dest: Path) -> None:
    """Accept a plain .db or a gzip-compressed one."""
    with src.open("rb") as f:
        magic = f.read(2)
    if magic == GZIP_MAGIC:
        with gzip.open(src, "rb") as zin, dest.open("wb") as out:
            shutil.copyfileobj(zin, out, 1 << 20)
        src.unlink()
    else:
        src.replace(dest)


def stage_restore(data_dir: Path, candidate: Path) -> dict:
    info = validate_backup(candidate)
    candidate.replace(data_dir / PENDING_NAME)
    log.warning("database restore staged; applied at next start", extra=info)
    return info


def apply_pending_restore(db_path: Path, backup_dir: Path) -> Path | None:
    """Called at start-up before the database is opened. Returns the safety copy, if any."""
    pending = db_path.parent / PENDING_NAME
    if not pending.exists():
        return None
    try:
        validate_backup(pending)
    except RestoreError as exc:
        log.error("pending restore rejected", extra={"error": str(exc)})
        pending.replace(pending.with_suffix(".rejected"))
        return None
    safety = None
    if db_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safety = backup_database(db_path, backup_dir / f"pre-restore-{stamp}-v{__version__}.db")
    for suffix in ("", "-wal", "-shm"):
        p = Path(f"{db_path}{suffix}")
        if p.exists():
            p.unlink()
    pending.replace(db_path)
    log.warning("database restored from backup", extra={"safety_copy": str(safety)})
    return safety


def list_backups(backup_dir: Path) -> list[dict]:
    if not backup_dir.is_dir():
        return []
    out = []
    for p in backup_dir.iterdir():
        if p.is_file() and BACKUP_NAME.match(p.name):
            st = p.stat()
            out.append({"name": p.name, "bytes": st.st_size, "modified": st.st_mtime})
    return sorted(out, key=lambda b: -b["modified"])


def backup_path(backup_dir: Path, name: str) -> Path:
    if not BACKUP_NAME.match(name):
        raise RestoreError("invalid backup name")
    p = (backup_dir / name).resolve()
    if p.parent != backup_dir.resolve() or not p.is_file():
        raise FileNotFoundError(name)
    return p
