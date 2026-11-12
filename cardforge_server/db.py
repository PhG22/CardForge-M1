"""PostgreSQL access: connection pool and schema migrations."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_LOCK = 72_631_001          # pg_advisory_lock key: one migrator at a time
_TX_LINE = re.compile(r"^\s*(BEGIN|COMMIT)\s*;\s*$", re.IGNORECASE | re.MULTILINE)


def open_pool(database_url: str, max_size: int = 10) -> ConnectionPool:
    """Each `with pool.connection() as conn:` block is one transaction:
    committed at the end, rolled back on an exception."""
    pool = ConnectionPool(database_url, min_size=1, max_size=max_size, open=False,
                          kwargs={"row_factory": dict_row})
    pool.open(wait=True, timeout=15)
    return pool


def migrate(database_url: str) -> list[str]:
    """Apply the migrations/NNN_*.sql files not applied yet, in order. Returns the new versions.

    Each file runs in one transaction together with its row in schema_migrations, so a
    failed migration leaves nothing behind. The files keep their own BEGIN/COMMIT lines
    (so they can also be run by hand with psql); those lines are skipped here.
    """
    applied_now = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                         " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = path.stem
                if version in done:
                    continue
                sql = _TX_LINE.sub("", path.read_text(encoding="utf-8"))
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                log.info("applied migration %s", version)
                applied_now.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return applied_now
