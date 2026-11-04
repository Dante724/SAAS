"""Migrations can't stall live traffic, and a failed CONCURRENTLY build doesn't leave a dead index.

1. Migration sessions on Postgres get a short lock_timeout (an ALTER waiting behind a long
   transaction otherwise queues every later query on that table behind it) and no
   statement_timeout (a big backfill or index build must not be cut off half-way).
2. `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (aqp01, ix144dh01, ix144p4, id144p4, sc144eo01)
   leaves an INVALID index behind when the build fails; a retry then skips it because the name
   exists. After every upgrade the env repairs invalid indexes with REINDEX CONCURRENTLY.

CONCURRENTLY builds wait for EVERY older open transaction in the same database. The suite's own
database can hold one (a connection an earlier test leaked, idle in transaction, in this very
process), which made these tests wait forever and hung a CI shard for hours. So each Postgres test
here runs in its own scratch database, with lock_timeout set, under a hard asyncio time bound.
"""
import asyncio
import contextlib
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


@contextlib.asynccontextmanager
async def _scratch_db():
    """A fresh, empty database nobody else connects to; dropped afterwards. Yields its URL."""
    base = _URL.rpartition("/")[0]
    name = f"tekly_mig_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(_URL, poolclass=NullPool,
                                connect_args={"server_settings": {"lock_timeout": "10s"}})
    try:
        async with admin.connect() as raw:
            c = await raw.execution_options(isolation_level="AUTOCOMMIT")
            try:
                await c.execute(text(f'CREATE DATABASE "{name}"'))
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"cannot create a scratch database: {exc}")
        yield f"{base}/{name}"
    finally:
        async with admin.connect() as raw:
            c = await raw.execution_options(isolation_level="AUTOCOMMIT")
            await c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


def _bounded_engine(url):
    # lock_timeout also bounds the CONCURRENTLY "wait for older transactions" phase.
    return create_async_engine(url, poolclass=NullPool, connect_args=ms.migration_connect_args(url))


async def _repair_scenario(url):
    eng = _bounded_engine(url)
    tbl, ix = "ixh_t", "ix_ixh_t_v"
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
        await eng.dispose()


@pg_only
@pytest.mark.asyncio
async def test_repair_rebuilds_an_invalid_concurrent_index():
    async with _scratch_db() as url:
        await asyncio.wait_for(_repair_scenario(url), timeout=60)


@pg_only
@pytest.mark.asyncio
async def test_repair_gives_up_instead_of_waiting_forever(monkeypatch):
    """An open transaction elsewhere in the database blocks REINDEX CONCURRENTLY. With the
    migration lock_timeout the repair logs and moves on; it never hangs the deploy."""
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT", "1s")
    async with _scratch_db() as url:
        eng = _bounded_engine(url)
        blocker_eng = create_async_engine(url, poolclass=NullPool)
        try:
            async with eng.connect() as raw:
                c = await raw.execution_options(isolation_level="AUTOCOMMIT")
                await c.execute(text("CREATE TABLE t (v int)"))
                await c.execute(text("CREATE INDEX t_v ON t (v)"))
                await c.execute(text(
                    "UPDATE pg_index SET indisvalid = false WHERE indexrelid = 't_v'::regclass"))
            async with blocker_eng.connect() as blocker:
                await blocker.execute(text("SELECT 1"))          # idle in transaction
                async with eng.connect() as raw:
                    c = await raw.execution_options(isolation_level="AUTOCOMMIT")
                    repaired = await asyncio.wait_for(c.run_sync(ms.repair_invalid_indexes), 30)
                assert "t_v" not in repaired
        except Exception as exc:  # noqa: BLE001
            if "permission denied" in str(exc):
                pytest.skip(f"cannot mark an index invalid: {exc}")
            raise
        finally:
            await blocker_eng.dispose()
            await eng.dispose()


def _alembic(url):
    env = dict(os.environ, ENVIRONMENT="test", SEED_DEMO="false", DATABASE_URL=url)
    env.pop("TEST_DATABASE_URL", None)
    return subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=300)


async def _upgrade_scenario(url):
    r = await asyncio.to_thread(_alembic, url)
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
        r = await asyncio.to_thread(_alembic, url)
        assert r.returncode == 0, r.stderr[-3000:]
        async with mig.connect() as c:
            assert await _invalid(c, "ix_postings_org_id_created_at") is False
    finally:
        await mig.dispose()


@pg_only
@pytest.mark.asyncio
async def test_upgrade_from_empty_postgres_and_repairs_invalid_index_on_rerun():
    """`alembic upgrade head` builds a fresh Postgres DB with the new session settings, and a rerun
    (the deploy retry) turns an INVALID index from a failed CONCURRENTLY build back into a valid one."""
    async with _scratch_db() as url:
        await asyncio.wait_for(_upgrade_scenario(url), timeout=600)
