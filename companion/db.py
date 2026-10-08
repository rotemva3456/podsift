"""One SQLite file for every feature, upgraded in place by numbered migrations.

A feature registers its schema at module level and never edits it after it is committed:

    register_migration("notes", 1, "CREATE TABLE IF NOT EXISTS notes (...)")
    register_migration("notes", 2, "ALTER TABLE notes ADD COLUMN ...")

At start, ``migrate`` applies every pending migration once, in (feature, version) order, each
inside its own transaction, and records it in ``schema_migrations``. A failing migration rolls
back completely and stops the start with ``MigrationError``. A feature's migrations change only
that feature's tables; to change another feature's table, add the next version under that
feature's name. Every table with user data has ``user_id TEXT NOT NULL DEFAULT 'default'``.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_FEATURE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MIGRATIONS: dict[tuple[str, int], str] = {}
_TRANSACTION_CONTROL = re.compile(r"^\s*(BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE)\b", re.IGNORECASE)


class MigrationError(RuntimeError):
    """The database could not be upgraded; the message says what to do."""


def register_migration(feature: str, version: int, sql: str) -> None:
    """Register schema change ``version`` (1, 2, ...) of ``feature``. Call it at module level."""
    if not _FEATURE.match(feature):
        raise ValueError(f"Migration feature names are lowercase words, not {feature!r}.")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"{feature} migration versions are whole numbers from 1, not {version!r}.")
    if not sql.strip():
        raise ValueError(f"{feature} v{version} has no SQL.")
    for statement in _statements(sql):
        if _TRANSACTION_CONTROL.match(statement):
            raise ValueError(f"{feature} v{version}: leave BEGIN/COMMIT out; each migration already runs in a transaction.")
    known = _MIGRATIONS.get((feature, version))
    if known is not None and known != sql:
        raise ValueError(f"{feature} v{version} is registered twice with different SQL. Add the next version instead.")
    _MIGRATIONS[(feature, version)] = sql


def registered() -> list[tuple[str, int]]:
    return sorted(_MIGRATIONS)


def connect(path: Path | str) -> sqlite3.Connection:
    """A connection with row access by name. FastAPI may use it from another worker thread."""
    db = sqlite3.connect(path, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def migrate(path: Path | str) -> list[tuple[str, int]]:
    """Apply every pending migration once. Returns the (feature, version) pairs applied now."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[tuple[str, int]] = []
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            feature TEXT NOT NULL, version INTEGER NOT NULL, checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL, PRIMARY KEY (feature, version))""")
        done = {(feature, version): checksum for feature, version, checksum
                in db.execute("SELECT feature, version, checksum FROM schema_migrations")}
        for (feature, version), sql in sorted(_MIGRATIONS.items()):
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if (feature, version) in done:
                if done[(feature, version)] != checksum:
                    log.warning("%s v%s changed after it was applied to %s. Committed migrations must "
                                "not change; add the next version instead.", feature, version, path)
                continue
            try:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM schema_migrations WHERE feature=? AND version=?",
                              (feature, version)).fetchone():
                    db.execute("COMMIT")  # another copy of the app applied it a moment ago
                    continue
                for statement in _statements(sql):
                    db.execute(statement)
                db.execute("INSERT INTO schema_migrations (feature, version, checksum, applied_at) VALUES (?,?,?,?)",
                           (feature, version, checksum, datetime.now(timezone.utc).isoformat()))
                db.execute("COMMIT")
            except sqlite3.Error as exc:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise MigrationError(
                    f"Could not upgrade the database {path}: {feature} v{version} failed ({exc}). "
                    "That change was rolled back and your data is as it was before it. "
                    "Fix the migration, or restore a backup of the file, then start again.") from exc
            applied.append((feature, version))
            log.info("Applied migration %s v%s to %s", feature, version, path)
    finally:
        db.close()
    return applied


def _statements(sql: str):
    """Split a script into single statements without breaking strings or trigger bodies."""
    buffer = ""
    for piece in sql.split(";"):
        buffer += piece + ";"
        if sqlite3.complete_statement(buffer):
            if buffer.strip(" \t\r\n;"):
                yield buffer
            buffer = ""
    if buffer.strip(" \t\r\n;"):
        yield buffer
