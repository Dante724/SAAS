"""Postgres lock paths, each exercised with two independent sessions or connections.

The suite normally runs on SQLite, which serialises every writer, so these four Postgres-only
mechanisms had no behavioural test (hardening council Q5):

- ``core.jobs.run_exclusive``: a session advisory lock elects one periodic-job runner;
- ``db.database.insert_ignore``: ``ON CONFLICT DO NOTHING`` lets the losing seed skip quietly;
- ``services.serialized.allocate_serialized_item``: ``FOR UPDATE SKIP LOCKED`` hands concurrent
  allocators different units instead of queueing them on one row;
- ``services.admin_log.write``: ``pg_advisory_xact_lock`` keeps the hash chain linear.

Every wait is bounded, so a lock that never comes back fails the test instead of hanging CI.
"""
import asyncio
import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.core import jobs
from backend.db.database import insert_ignore
from backend.models import AdminActionLog, Location, NumberSequence, Order, Organisation, Part, SerializedItem
from backend.services import admin_log
from backend.services.serialized import allocate_serialized_item

_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
pg_only = pytest.mark.skipif(not _URL.startswith("postgresql"), reason="Postgres row and advisory locks")
WAIT = 10          # seconds: generous for a lock hand-over, short enough to fail a hung test fast
SETTLE = 0.5       # long enough for the second statement to reach the server and block


def _sessions(db):
    """Two sessions on the test engine (NullPool): each one is its own server connection."""
    return async_sessionmaker(db.bind, expire_on_commit=False)


async def _shop(db) -> tuple[str, str, str]:
    org = Organisation(name="Lock Co")
    db.add(org)
    await db.flush()
    loc = Location(org_id=org.id, name="Main")
    part = Part(org_id=org.id, name="Screen")
    db.add_all([loc, part])
    await db.flush()
    order = Order(org_id=org.id, order_number="O-LOCK", location_id=loc.id, status="new")
    db.add(order)
    await db.commit()
    return org.id, part.id, order.id


@pg_only
@pytest.mark.asyncio
async def test_run_exclusive_second_runner_skips_while_the_first_holds_the_lock():
    first_in, release = asyncio.Event(), asyncio.Event()
    ran: list[str] = []

    async def slow():
        ran.append("first")
        first_in.set()
        await asyncio.wait_for(release.wait(), WAIT)

    async def rival():
        ran.append("second")

    first = asyncio.create_task(jobs.run_exclusive("pg-locks-test", slow))
    await asyncio.wait_for(first_in.wait(), WAIT)
    try:
        # Another worker's tick arrives while the first run is still going: it skips, at once.
        assert await asyncio.wait_for(jobs.run_exclusive("pg-locks-test", rival), WAIT) is False
        assert ran == ["first"]
    finally:
        release.set()
    assert await asyncio.wait_for(first, WAIT) is True
    # The lock went with the run: the next tick runs.
    assert await asyncio.wait_for(jobs.run_exclusive("pg-locks-test", rival), WAIT) is True
    assert ran == ["first", "second"]


@pg_only
@pytest.mark.asyncio
async def test_insert_ignore_loser_of_a_seed_race_inserts_nothing(db):
    org_id, _, _ = await _shop(db)
    S = _sessions(db)
    values = dict(org_id=org_id, doc_type="order", scope_key="org", template="O-{n}")
    async with S() as a, S() as b:
        assert await insert_ignore(a, NumberSequence, **values) is True
        # b's insert waits on a's uncommitted row (same unique key) rather than failing or winning.
        loser = asyncio.create_task(insert_ignore(b, NumberSequence, **values))
        await asyncio.sleep(SETTLE)
        assert not loser.done()
        await a.commit()
        assert await asyncio.wait_for(loser, WAIT) is False      # 0 rows, no IntegrityError
        await b.commit()
    assert (await db.execute(select(func.count()).select_from(NumberSequence))).scalar() == 1


@pg_only
@pytest.mark.asyncio
async def test_skip_locked_gives_two_concurrent_allocators_different_units(db):
    org_id, part_id, order_id = await _shop(db)
    db.add_all([SerializedItem(org_id=org_id, part_id=part_id, serial_number=f"SN-{i}") for i in (1, 2)])
    await db.commit()
    S = _sessions(db)
    async with S() as a, S() as b:
        first = await allocate_serialized_item(a, org_id=org_id, part_id=part_id, order_id=order_id)
        # a holds its unit's row lock and has not committed: b must not queue behind it (a plain
        # FOR UPDATE would block here until a commits), it takes the other unit at once.
        second = await asyncio.wait_for(
            allocate_serialized_item(b, org_id=org_id, part_id=part_id, order_id=order_id), WAIT)
        assert second.id != first.id
        await a.commit()
        await b.commit()
    rows = (await db.execute(select(SerializedItem.id, SerializedItem.status, SerializedItem.order_id))).all()
    assert sorted(r.status for r in rows) == ["allocated_to_order", "allocated_to_order"]
    assert {r.order_id for r in rows} == {order_id}


@pg_only
@pytest.mark.asyncio
async def test_admin_log_advisory_lock_serialises_writers_so_the_chain_stays_linear(db):
    S = _sessions(db)
    async with S() as a, S() as b:
        row_a = await admin_log.write(a, admin=None, action="pg-locks-a")
        # b takes the same transaction-level advisory lock: it waits until a commits, so it reads
        # a's row as the chain head instead of forking the chain from the row before it.
        waiting = asyncio.create_task(admin_log.write(b, admin=None, action="pg-locks-b"))
        await asyncio.sleep(SETTLE)
        assert not waiting.done()
        await a.commit()
        row_b = await asyncio.wait_for(waiting, WAIT)
        await b.commit()
    assert row_b.prev_hash == row_a.row_hash
    rows = (await db.execute(select(AdminActionLog).order_by(AdminActionLog.id))).scalars().all()
    assert [r.action for r in rows] == ["pg-locks-a", "pg-locks-b"]
    assert rows[0].prev_hash is None and rows[1].prev_hash == rows[0].row_hash
