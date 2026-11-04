"""Periodic-job exclusivity survives a job that runs longer than the idle-in-transaction timeout.

Regression: run_exclusive took its session advisory lock on a connection that auto-began a
transaction and then sat idle in it while the job ran on other connections. Postgres'
idle_in_transaction_session_timeout (DB_IDLE_IN_TX_TIMEOUT_MS, 60s in production) killed that
connection, which released the lock: a second worker/replica then ran the same sweep at the
same time, and the first one's unlock in `finally` raised on the dead connection.
"""
import asyncio
import logging
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.core import jobs

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="Postgres advisory locks")


def _engine(idle_ms: int):
    # Same knob the app sets from DB_IDLE_IN_TX_TIMEOUT_MS, shrunk so the test runs in seconds.
    return create_async_engine(_URL, poolclass=NullPool, connect_args={
        "server_settings": {"idle_in_transaction_session_timeout": str(idle_ms)}})


async def _kill_lock_holder(eng, name: str) -> int:
    """Terminate only the backend holding this job's advisory lock (as a DB restart would)."""
    key = jobs._lock_key(name)
    async with eng.connect() as c:
        return (await c.execute(text(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid::bigint = :hi AND objid::bigint = :lo AND objsubid = 1"),
            {"hi": (key >> 32) & 0xFFFFFFFF, "lo": key & 0xFFFFFFFF})).scalar()


@pg_only
@pytest.mark.asyncio
async def test_lock_held_past_idle_in_transaction_timeout(monkeypatch):
    eng = _engine(500)
    monkeypatch.setattr("backend.db.database.engine", eng)
    second_ran = []

    async def rival():
        second_ran.append(1)

    async def long_job():
        await asyncio.sleep(1.5)          # three times the idle-in-transaction timeout
        # Another worker's tick arrives while this run is still going: it must skip.
        assert await jobs.run_exclusive("lock-hardening", rival) is False

    try:
        assert await jobs.run_exclusive("lock-hardening", long_job) is True
        assert second_ran == []
        # The lock was released at the end: the next tick runs.
        assert await jobs.run_exclusive("lock-hardening", rival) is True
        assert second_ran == [1]
    finally:
        await eng.dispose()


@pg_only
@pytest.mark.asyncio
async def test_dead_lock_connection_does_not_mask_the_job_error(monkeypatch, caplog):
    """If the lock connection dies anyway (DB restart, network), the unlock failure is logged and
    the job's own exception is what the caller sees."""
    eng = _engine(0)
    monkeypatch.setattr("backend.db.database.engine", eng)

    async def job_that_fails():
        assert await _kill_lock_holder(eng, "lock-hardening-dead") == 1
        raise ValueError("job failed")

    try:
        with caplog.at_level(logging.WARNING, logger="tekly.jobs"):
            with pytest.raises(ValueError, match="job failed"):
                await jobs.run_exclusive("lock-hardening-dead", job_that_fails)
        assert any("unlock" in r.getMessage() for r in caplog.records)
    finally:
        await eng.dispose()


@pg_only
@pytest.mark.asyncio
async def test_dead_lock_connection_after_success_is_logged_not_raised(monkeypatch, caplog):
    eng = _engine(0)
    monkeypatch.setattr("backend.db.database.engine", eng)

    async def job():
        assert await _kill_lock_holder(eng, "lock-hardening-dead2") == 1

    try:
        with caplog.at_level(logging.WARNING, logger="tekly.jobs"):
            assert await jobs.run_exclusive("lock-hardening-dead2", job) is True
        assert any("unlock" in r.getMessage() for r in caplog.records)
    finally:
        await eng.dispose()
