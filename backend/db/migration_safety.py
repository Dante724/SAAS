"""Postgres safety for `alembic upgrade`, used by backend/migrations/env.py.

Session settings: a migration's ALTER TABLE needs an ACCESS EXCLUSIVE lock. If it waits behind a
long-running transaction, every later query on that table queues behind the waiting ALTER, so the
live app stalls for as long as the migration waits. A short lock_timeout makes the migration fail
fast instead (rerun the deploy). statement_timeout is off so a large backfill or index build is
never cut off half-way.

Invalid indexes: several migrations (aqp01, ix144dh01, ix144p4, id144p4, sc144eo01) build indexes
with `CREATE INDEX CONCURRENTLY IF NOT EXISTS`. If such a build fails (lock_timeout, deadlock,
cancelled deploy), Postgres keeps the index but marks it INVALID: it is maintained on every write
yet never used for reads, and the retry skips it because the name exists. After every upgrade
`repair_invalid_indexes` rebuilds those with REINDEX CONCURRENTLY. That restores the index the
migration already declared; it never changes the schema.
"""
import logging
import os

from sqlalchemy import text

log = logging.getLogger("tekly.migrations")

DEFAULT_LOCK_TIMEOUT = "5s"


def migration_connect_args(url: str) -> dict:
    """asyncpg connect args for migration sessions; empty for SQLite."""
    if not (url or "").startswith("postgresql"):
        return {}
    return {"server_settings": {
        "lock_timeout": os.environ.get("MIGRATION_LOCK_TIMEOUT") or DEFAULT_LOCK_TIMEOUT,
        "statement_timeout": "0",
        "idle_in_transaction_session_timeout": "0",
    }}


_INVALID_SQL = text("""
    SELECT c.relname
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT i.indisvalid AND n.nspname = current_schema()
    ORDER BY c.relname
""")


def repair_invalid_indexes(conn) -> list[str]:
    """Rebuild every INVALID index in the current schema. `conn` must be a sync Connection in
    AUTOCOMMIT (REINDEX CONCURRENTLY can't run in a transaction). Returns the names rebuilt.

    Leftovers of a failed REINDEX CONCURRENTLY itself (`*_ccnew*` / `*_ccold*`) are not touched:
    they are logged for an operator to DROP INDEX CONCURRENTLY, since rebuilding them would
    keep a duplicate index around."""
    if conn.dialect.name != "postgresql":
        return []
    repaired = []
    for (name,) in conn.execute(_INVALID_SQL).all():
        if "_ccnew" in name or "_ccold" in name:
            log.warning("invalid index %s is a failed REINDEX CONCURRENTLY leftover; "
                        "drop it with DROP INDEX CONCURRENTLY", name)
            continue
        log.warning("index %s is INVALID (a CONCURRENTLY build failed); rebuilding it", name)
        # REINDEX CONCURRENTLY waits for every older open transaction in this database; the
        # migration session's lock_timeout bounds that wait. If it gives up, the migrations have
        # already committed: log it and leave the index for the next upgrade, don't fail the deploy.
        try:
            conn.execute(text(f'REINDEX INDEX CONCURRENTLY "{name}"'))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not rebuild invalid index %s (%s); the next upgrade retries it",
                        name, type(exc).__name__)
            continue
        repaired.append(name)
    return repaired
