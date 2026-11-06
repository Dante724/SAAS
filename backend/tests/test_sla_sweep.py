"""status-sla-sweep (services/sla.py): behaviour pins + query-shape guard.

The equivalence tests pin exactly which open orders breach and which one-time feed events the
sweep writes (terminal statuses, statuses without a limit, null status_entered_at, the minute
boundary, dedupe, re-entering a status, several shops). The shape tests guard council finding
M21: the sweep used to load every open order of every shop into Python every 10 minutes, and
ran two extra queries per breaching order to dedupe.
"""
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from backend.models import EventFeedItem, Location, Order, OrderStatus, Organisation
from backend.services.dates import now_utc
from backend.services.sla import find_breaches, sweep_breaches

pytestmark = pytest.mark.asyncio


async def _shop(db, statuses):
    """A shop with one location and the given statuses: [(key, limit, is_terminal), ...]."""
    org = Organisation(name="SLA shop")
    db.add(org)
    await db.flush()
    loc = Location(org_id=org.id, name="Main")
    db.add(loc)
    for key, limit, terminal in statuses:
        db.add(OrderStatus(org_id=org.id, key=key, name=key.title(), time_limit_min=limit,
                           is_terminal=terminal))
    await db.flush()
    return org.id, loc.id


_seq = [0]


async def _order(db, org_id, loc_id, status, entered, closed_at=None):
    _seq[0] += 1
    o = Order(org_id=org_id, location_id=loc_id, order_number=f"SLA-{_seq[0]:05d}",
              status=status, status_entered_at=entered, closed_at=closed_at)
    db.add(o)
    await db.flush()
    if entered is None:  # the column defaults to now; force a real NULL
        o.status_entered_at = None
        await db.flush()
    return o


async def _sla_events(db, order_id):
    return (await db.execute(select(EventFeedItem).where(
        EventFeedItem.entity_id == order_id).order_by(EventFeedItem.created_at))).scalars().all()


async def test_which_orders_breach(db):
    now = now_utc()
    org, loc = await _shop(db, [
        ("work", 30, False), ("done", 30, True), ("nolimit", None, False), ("zero", 0, False)])
    over = await _order(db, org, loc, "work", now - timedelta(minutes=45))
    await _order(db, org, loc, "work", now - timedelta(minutes=10))           # under
    await _order(db, org, loc, "done", now - timedelta(minutes=500))          # terminal
    await _order(db, org, loc, "nolimit", now - timedelta(minutes=500))       # no limit
    await _order(db, org, loc, "zero", now - timedelta(minutes=500))          # limit 0 = none
    await _order(db, org, loc, "unknown", now - timedelta(minutes=500))       # not a status
    await _order(db, org, loc, "work", None)                                  # never entered
    await _order(db, org, loc, "work", now - timedelta(minutes=500),
                 closed_at=now - timedelta(minutes=1))                        # closed
    await db.commit()

    assert await find_breaches(db, org, now=now) == [{
        "order_id": over.id, "order_number": over.order_number, "status": "work",
        "status_name": "Work", "minutes_in_status": 45, "limit_min": 30, "over_by_min": 15,
    }]


async def test_minute_boundary(db):
    """Breach means whole minutes in status > limit: exactly 30m (or 30m59.999s) is not over a
    30m limit, exactly 31m is."""
    now = now_utc()
    org, loc = await _shop(db, [("work", 30, False)])
    at = await _order(db, org, loc, "work", now - timedelta(minutes=30))
    just_under = await _order(db, org, loc, "work", now - timedelta(minutes=31) + timedelta(microseconds=1))
    first = await _order(db, org, loc, "work", now - timedelta(minutes=31))
    await db.commit()

    got = {b["order_id"]: b for b in await find_breaches(db, org, now=now)}
    assert at.id not in got and just_under.id not in got
    assert set(got) == {first.id}
    assert got[first.id]["minutes_in_status"] == 31 and got[first.id]["over_by_min"] == 1


async def test_sweep_writes_one_event_per_breach_and_dedupes(db):
    now = now_utc()
    org, loc = await _shop(db, [("work", 30, False)])
    a = await _order(db, org, loc, "work", now - timedelta(minutes=95))
    b = await _order(db, org, loc, "work", now - timedelta(minutes=40))
    under = await _order(db, org, loc, "work", now - timedelta(minutes=5))
    await db.commit()

    assert await sweep_breaches(db, now=now) == 2
    ev = await _sla_events(db, a.id)
    assert len(ev) == 1
    e = ev[0]
    assert (e.org_id, e.entity_type, e.item_type, e.visibility, e.author_name) == (
        org, "order", "system", "internal", "system")
    assert e.body == "[sla] SLA breach: 65m over the 30m limit for status 'Work'."
    assert [x.body for x in await _sla_events(db, b.id)] == [
        "[sla] SLA breach: 10m over the 30m limit for status 'Work'."]
    assert await _sla_events(db, under.id) == []

    # Already flagged since it entered the status: never re-announced.
    assert await sweep_breaches(db, now=now) == 0
    # Hours later only the order that has since crossed its limit is new.
    assert await sweep_breaches(db, now=now + timedelta(hours=3)) == 1
    assert len(await _sla_events(db, a.id)) == 1 and len(await _sla_events(db, b.id)) == 1
    assert [x.body for x in await _sla_events(db, under.id)] == [
        "[sla] SLA breach: 155m over the 30m limit for status 'Work'."]


async def test_dedupe_rules(db):
    """What counts as 'already flagged': an [sla] system event on THIS order created at or after
    it entered the status. Older ones, other orders' ones, non-system or non-[sla] ones don't."""
    now = now_utc()
    org, loc = await _shop(db, [("work", 30, False)])
    entered = now - timedelta(minutes=90)
    flagged_same_instant = await _order(db, org, loc, "work", entered)
    flagged_before_entry = await _order(db, org, loc, "work", entered)
    other_kind = await _order(db, org, loc, "work", entered)
    other_order = await _order(db, org, loc, "work", entered)

    def ev(order_id, created, body="[sla] SLA breach: old", item_type="system"):
        db.add(EventFeedItem(org_id=org, entity_type="order", entity_id=order_id,
                             item_type=item_type, visibility="internal", body=body,
                             author_name="system", created_at=created))

    ev(flagged_same_instant.id, entered)                            # counts (>=)
    ev(flagged_before_entry.id, entered - timedelta(seconds=1))     # before entry: doesn't
    ev(other_kind.id, entered + timedelta(minutes=1), item_type="comment")
    ev(other_kind.id, entered + timedelta(minutes=1), body="SLA breach, no marker")
    ev(other_kind.id, entered + timedelta(minutes=1), body="note [sla] inside")
    ev("some-other-order", entered + timedelta(minutes=1))
    await db.commit()

    assert await sweep_breaches(db, now=now) == 3
    assert len(await _sla_events(db, flagged_same_instant.id)) == 1
    assert len(await _sla_events(db, flagged_before_entry.id)) == 2
    assert len(await _sla_events(db, other_kind.id)) == 4
    assert len(await _sla_events(db, other_order.id)) == 1


async def test_re_entering_a_status_flags_again(db):
    now = now_utc()
    org, loc = await _shop(db, [("work", 30, False)])
    o = await _order(db, org, loc, "work", now - timedelta(minutes=200))
    db.add(EventFeedItem(org_id=org, entity_type="order", entity_id=o.id, item_type="system",
                         visibility="internal", author_name="system",
                         body="[sla] SLA breach: earlier", created_at=now - timedelta(minutes=150)))
    await db.commit()
    assert await sweep_breaches(db, now=now) == 0           # flagged since it entered

    o.status_entered_at = now - timedelta(minutes=60)       # left and came back
    await db.commit()
    assert await sweep_breaches(db, now=now) == 1
    assert [e.body for e in await _sla_events(db, o.id)][-1] == (
        "[sla] SLA breach: 30m over the 30m limit for status 'Work'.")


async def test_multiple_shops(db):
    now = now_utc()
    a_org, a_loc = await _shop(db, [("work", 30, False)])
    b_org, b_loc = await _shop(db, [("work", None, False)])          # same key, no limit
    c_org, c_loc = await _shop(db, [("work", 120, False), ("qa", 10, False)])
    a1 = await _order(db, a_org, a_loc, "work", now - timedelta(minutes=60))
    await _order(db, b_org, b_loc, "work", now - timedelta(minutes=600))
    await _order(db, c_org, c_loc, "work", now - timedelta(minutes=60))   # under c's 120
    c2 = await _order(db, c_org, c_loc, "qa", now - timedelta(minutes=11))
    c3 = await _order(db, c_org, c_loc, "work", now - timedelta(minutes=125))
    await db.commit()

    assert [b["order_id"] for b in await find_breaches(db, a_org, now=now)] == [a1.id]
    assert await find_breaches(db, b_org, now=now) == []
    assert {b["order_id"] for b in await find_breaches(db, c_org, now=now)} == {c2.id, c3.id}

    assert await sweep_breaches(db, now=now) == 3
    rows = (await db.execute(select(EventFeedItem.org_id, EventFeedItem.entity_id).where(
        EventFeedItem.body.like("[sla]%")))).all()
    assert sorted(rows) == sorted([(a_org, a1.id), (c_org, c2.id), (c_org, c3.id)])
    assert await sweep_breaches(db, now=now) == 0


# ── query shape (M21) ─────────────────────────────────────────────────────────

class _Spy:
    """Counts SQL statements and Order rows loaded while active."""

    def __init__(self, db):
        self.engine = db.get_bind()
        self.statements: list[str] = []
        self.orders_loaded = 0

    def _stmt(self, conn, cursor, statement, params, context, executemany):
        self.statements.append(statement)

    def _load(self, target, context):
        self.orders_loaded += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._stmt)
        event.listen(Order, "load", self._load)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._stmt)
        event.remove(Order, "load", self._load)

    def selects(self):
        return [s for s in self.statements if s.lstrip().upper().startswith("SELECT")]


async def test_sweep_never_reads_orders_of_shops_without_limits(db):
    """A shop with no status that has a time limit can't breach: the sweep must not read its
    orders at all (it used to load every open order of every shop)."""
    now = now_utc()
    org, loc = await _shop(db, [("work", None, False), ("done", None, True)])
    for i in range(60):
        await _order(db, org, loc, "work", now - timedelta(minutes=600 + i))
    await db.commit()

    with _Spy(db) as spy:
        assert await sweep_breaches(db, now=now) == 0
    assert spy.orders_loaded == 0
    assert not [s for s in spy.statements if " orders" in s.lower()], spy.statements


async def test_sweep_statement_count_independent_of_order_count(db):
    """Dedupe happens in SQL: a sweep over N already-flagged breaching orders runs the same number
    of statements for N=3 as for N=40 (it used to run two extra queries per breaching order)."""
    now = now_utc()

    org, loc = await _shop(db, [("work", 30, False)])

    async def grow_to(n):
        have = len((await db.execute(select(Order.id).where(Order.org_id == org))).all())
        for _ in range(n - have):
            await _order(db, org, loc, "work", now - timedelta(minutes=90))
        await db.commit()
        assert await sweep_breaches(db, now=now) == n - have    # flag the new ones once
        with _Spy(db) as spy:
            assert await sweep_breaches(db, now=now) == 0       # all already flagged
        return spy

    small = await grow_to(3)
    big = await grow_to(40)
    assert len(big.selects()) == len(small.selects()), (small.selects(), big.selects())
    assert big.orders_loaded == 0
