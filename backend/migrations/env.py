"""Alembic environment (async-aware).

Targets the live ORM metadata (Base.metadata) so autogenerate stays in sync
with backend/models, and reads the database URL from application settings so
there is a single source of truth and no secrets in the ini file.
"""
import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Make the repo root importable regardless of where alembic is invoked from.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.config import settings          # noqa: E402
from backend.db.database import Base          # noqa: E402
from backend.db.migration_safety import migration_connect_args, repair_invalid_indexes  # noqa: E402
from backend.models import all_models         # noqa: E402,F401 — registers tables

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,          # required for SQLite ALTER support
        compare_type=True,
        # Each migration commits on its own (final review P2-1). In one shared transaction the
        # ACCESS EXCLUSIVE locks an early migration takes (dec08's ALTER TYPEs, w2b01's CHECKs,
        # w4i01's add_column ...) stayed held through every later migration, including dbi01's and
        # w4s01's read-only preflight scans, so login and every request queued behind them. It is
        # also what alembic recommends with autocommit_block(): that block commits whatever came
        # before it anyway. A failed migration rolls back alone; the ones before it stay applied.
        transaction_per_migration=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    # Postgres: lock_timeout so a migration waiting on a table lock fails fast instead of queueing
    # live traffic behind it; no statement_timeout so a long backfill/index build isn't cut off.
    # See backend/db/migration_safety.py.
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=migration_connect_args(settings.DATABASE_URL),
    )
    def _do_run(sync_conn):
        _configure(sync_conn)
        # Alembic must own the transactions (one per migration, see _configure): migrations that
        # need op.get_context().autocommit_block() (CREATE INDEX CONCURRENTLY on Postgres) assert
        # without one.
        with context.begin_transaction():
            context.run_migrations()

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run)
    if connectable.dialect.name == "postgresql":
        # A failed CREATE INDEX CONCURRENTLY leaves an INVALID index the IF NOT EXISTS retry skips;
        # rebuild those now (REINDEX CONCURRENTLY needs AUTOCOMMIT, hence its own connection).
        async with connectable.connect() as raw:
            conn = await raw.execution_options(isolation_level="AUTOCOMMIT")
            await conn.run_sync(repair_invalid_indexes)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
