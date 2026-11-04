"""Migrations can't stall live traffic, and a failed CONCURRENTLY build doesn't leave a dead index.

1. Migration sessions on Postgres get a short lock_timeout (an ALTER waiting behind a long
   transaction otherwise queues every later query on that table behind it) and no
   statement_timeout (a big backfill or index build must not be cut off half-way).
2. `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (aqp01, ix144dh01, ix144p4, id144p4, sc144eo01)
   leaves an INVALID index behind when the build fails; a retry then skips it because the name
   exists. After every upgrade the env repairs invalid indexes with REINDEX CONCURRENTLY.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.db import migration_safety as ms

REPO = Path(__file__).resolve().parents[2]
_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="Postgres only")


def test_migration_connect_args_postgres_only(monkeypatch):
    monkeypatch.delenv("MIGRATION_LOCK_TIMEOUT", raising=False)
    s = ms.migration_connect_args("postgresql+asyncpg://u@h/db")["server_settings"]
    assert s["lock_timeout"] == "5s"
    assert s["statement_timeout"] == "0"
    assert ms.migration_connect_args("sqlite+aiosqlite:///./x.db") == {}
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT", "15s")
    assert ms.migration_connect_args("postgresql+asyncpg://u@h/db")["server_settings"]["lock_timeout"] == "15s"


async def _invalid(conn, name):
    return (await conn.execute(text(
        "SELECT NOT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = :n"), {"n": name})).scalar()


@pg_only
@pytest.mark.asyncio
async def test_repair_rebuilds_an_invalid_concurrent_index():
    eng = create_async_engine(_URL, poolclass=NullPool)
    tbl = f"_ixh_{uuid.uuid4().hex[:8]}"
    ix = f"ix_{tbl}_v"
    try:
        async with eng.connect() as raw:
            c = await raw.execution_options(isolation_level="AUTOCOMMIT")
            await c.execute(text(f"CREATE TABLE {tbl} (id int, v int)"))
            await c.execute(text(f"INSERT INTO {tbl} VALUES (1, 1), (2, 1)"))
            # A concurrent build that fails part-way leaves the index behind, INVALID.
            with pytest.raises(IntegrityError):
                await c.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY {ix} ON {tbl} (v)"))
            assert await _invalid(c, ix) is True
            # The retry the migrations do is a no-op: the name exists.
            await c.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {ix} ON {tbl} (v)"))
            assert await _invalid(c, ix) is True
            await c.execute(text(f"DELETE FROM {tbl} WHERE id = 2"))

            repaired = await c.run_sync(ms.repair_invalid_indexes)
            assert ix in repaired
            assert await _invalid(c, ix) is False
            # Nothing left to repair: a second pass is a cheap no-op.
            assert ix not in await c.run_sync(ms.repair_invalid_indexes)
    finally:
        async with eng.connect() as raw:
            c = await raw.execution_options(isolation_level="AUTOCOMMIT")
            await c.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
        await eng.dispose()


def _alembic(url):
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=600)


@pg_only
@pytest.mark.asyncio
async def test_upgrade_from_empty_postgres_and_repairs_invalid_index_on_rerun():
    """`alembic upgrade head` builds a fresh Postgres DB with the new session settings, and a rerun
    (the deploy retry) turns an INVALID index from a failed CONCURRENTLY build back into a valid one."""
    base, _, _db = _URL.rpartition("/")
    name = f"tekly_mig_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(_URL, poolclass=NullPool)
    try:
        async with admin.connect() as raw:
            c = await raw.execution_options(isolation_level="AUTOCOMMIT")
            try:
                await c.execute(text(f'CREATE DATABASE "{name}"'))
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"cannot create a scratch database: {exc}")
        url = f"{base}/{name}"
        r = _alembic(url)
        assert r.returncode == 0, r.stderr[-3000:]

        mig = create_async_engine(url, poolclass=NullPool)
        try:
            async with mig.connect() as raw:
                c = await raw.execution_options(isolation_level="AUTOCOMMIT")
                # Simulate the failed build of an aqp01 index (superuser-only catalog write).
                try:
                    await c.execute(text(
                        "UPDATE pg_index SET indisvalid = false WHERE indexrelid = "
                        "'ix_postings_org_id_created_at'::regclass"))
                except Exception as exc:  # noqa: BLE001
                    pytest.skip(f"cannot mark an index invalid: {exc}")
                assert await _invalid(c, "ix_postings_org_id_created_at") is True
            r = _alembic(url)
            assert r.returncode == 0, r.stderr[-3000:]
            async with mig.connect() as c:
                assert await _invalid(c, "ix_postings_org_id_created_at") is False
        finally:
            await mig.dispose()
    finally:
        async with admin.connect() as raw:
            c = await raw.execution_options(isolation_level="AUTOCOMMIT")
            await c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()
