"""A test can't leave work running past its end, and a hung test fails the run instead of hanging it.

The CI hang this guards against: a race test's gather() raised while the other request was still
mid-transaction. pytest-asyncio then closed the loop without cancelling it, so that request's
connection stayed idle in transaction holding a row lock, and the next test's cleanup (DELETE FROM
every table) waited on it forever: the shard printed nothing more until the 45-minute job timeout.
"""
import asyncio
import os
import time

import pytest
from sqlalchemy import text

from backend.tests import db_isolation, hang_guard

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")


@pytest.mark.asyncio
async def test_a_request_still_running_at_the_end_is_cancelled_and_lets_go_of_its_locks(
        client, tenant, monkeypatch):
    from backend.routers import cash
    org = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    acc = (await client.post("/api/cash/accounts", json={"name": "Till", "payment_option": "cash"},
                             headers=tenant)).json()["id"]
    holding = asyncio.Event()
    lock = " FOR UPDATE" if _URL.startswith("postgresql") else ""     # SQLite has no row locks

    async def stuck(data, db, current):         # the request locks a row, then never finishes
        await db.execute(text(f"SELECT id FROM organisations WHERE id = :o{lock}"), {"o": org})
        holding.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(cash, "_create_transaction", stuck)
    req = asyncio.ensure_future(client.post("/api/cash/transactions", headers=tenant, json={
        "account_id": acc, "txn_type": "income", "amount": 5}))
    await asyncio.wait_for(holding.wait(), 10)

    cancelled, still_running = await hang_guard.cancel_leftover_tasks(timeout=10)

    assert req.done() and still_running == [], still_running
    assert len(cancelled) >= 1
    if _URL.startswith("postgresql"):
        # The row lock is gone and no session is left idle in a transaction.
        import asyncpg
        conn = await asyncpg.connect(db_isolation._asyncpg_dsn(_URL, _URL.rsplit("/", 1)[-1]))
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '2s'")
                assert await conn.fetchval("SELECT id FROM organisations WHERE id = $1 FOR UPDATE NOWAIT", org) == org
        finally:
            await conn.close()
        sessions = await db_isolation.activity(_URL)
        assert not [s for s in sessions if "idle in transaction" in s], sessions


def test_watchdog_names_the_hung_test_dumps_stacks_and_sessions_then_stops(tmp_path):
    import io
    import threading
    exited, fired, stream = [], threading.Event(), io.StringIO()

    def stop(code):
        exited.append(code)
        fired.set()
    report = tmp_path / "hang.txt"
    dog = hang_guard.Watchdog(0.2, str(report), diagnostics=lambda: ["pid=1 state=idle in transaction"],
                              exit_process=stop, stream=stream)
    dog.start("backend/tests/test_x.py::test_stuck")
    assert fired.wait(10)
    written = report.read_text()
    assert exited == [hang_guard.HANG_EXIT]
    assert "HANG: backend/tests/test_x.py::test_stuck ran over 0s" in written
    assert "Current thread" in written or "Thread 0x" in written          # every thread's stack
    assert "pid=1 state=idle in transaction" in written
    assert stream.getvalue().strip() == written.strip()                   # and on stderr


def test_watchdog_does_nothing_for_a_test_that_finishes_in_time(tmp_path):
    exited = []
    dog = hang_guard.Watchdog(0.3, str(tmp_path / "hang.txt"), exit_process=exited.append)
    dog.start("t")
    dog.cancel()
    time.sleep(0.5)
    assert exited == [] and not (tmp_path / "hang.txt").exists()


def test_the_controller_finds_its_workers_reports():
    import os
    path = hang_guard.report_path(os.getpid(), "gw7")
    try:
        open(path, "w").close()
        assert path in hang_guard.reports_for(os.getpid())
        assert path not in hang_guard.reports_for(os.getpid() + 1)
    finally:
        os.unlink(path)


def test_every_test_is_bounded_by_default(monkeypatch):
    monkeypatch.delenv("TEST_HANG_TIMEOUT_S", raising=False)
    assert 0 < hang_guard.hang_timeout_s() <= 900
    monkeypatch.setenv("TEST_HANG_TIMEOUT_S", "0")
    assert hang_guard.hang_timeout_s() == 0
