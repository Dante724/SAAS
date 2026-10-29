"""Tekly Sense sprint 3 edge cases (from the tester's review): who gets the credit, what counts,
who is compared, how review works, and the scorecard."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services import sense
from backend.tests.test_sense_sprint1 import _register, _staff
from backend.tests.test_sense_sprint2 import _emp_id
from backend.tests.test_sense_sprint3 import _account, _job, _loc, _team

PERMS = {"can_create_tickets": True, "can_set_discount": True, "can_edit_sale_price": True,
         "can_set_discount_type": True}


async def _closed(db, org_id, *, assigned, device_id, created, closed, total=100, **kw):
    """A job with exact timestamps (sprint 3's _job calls utcnow() twice, so created_at lands
    a few microseconds AFTER closed_at and the job counts as its own comeback)."""
    from backend.models import Order
    o = Order(org_id=org_id, order_number=f"C-{uuid.uuid4().hex[:6]}", location_id=await _loc(db, org_id),
              complaint="x", status="closed" if closed else "new", created_at=created, closed_at=closed,
              assigned_to_id=assigned, device_id=device_id, total=total, **kw)
    db.add(o)
    await db.flush()
    return o


async def _wd(db, org_id, now=None):
    db.expire_all()
    return await sense.watchdog(db, org_id, now=now)


def _kinds(res, kind):
    return {f["employee_id"]: f for f in res["flags"] if f["kind"] == kind}


async def _line(db, org_id, *, by, qty=1, price=100, disc=30, dtype="pct", days=1, archived=False, estimate=False):
    from backend.models import Order, OrderLine
    o = Order(org_id=org_id, order_number=f"Q-{uuid.uuid4().hex[:6]}", location_id=await _loc(db, org_id),
              complaint="x", status="new", created_at=datetime.utcnow() - timedelta(days=days),
              archived=archived, is_estimate=estimate)
    db.add(o)
    await db.flush()
    db.add(OrderLine(order_id=o.id, line_type="labour", kind="service", description="L", qty=qty,
                     unit_price=price, discount=disc, discount_type=dtype, discount_by_id=by, total=0))
    return o


async def _sale(db, org_id, *, seller, disc=0, whole_discount=0, voided=False, qty=1, price=100):
    from backend.models import Sale, SaleLine
    now = datetime.utcnow()
    s = Sale(org_id=org_id, location_id=await _loc(db, org_id), sale_number=f"S-{uuid.uuid4().hex[:5]}",
             seller_id=seller, total=qty * price, discount=whole_discount, created_at=now - timedelta(hours=1),
             voided_at=now if voided else None)
    db.add(s)
    await db.flush()
    db.add(SaleLine(sale_id=s.id, kind="service", description="POS", qty=qty, unit_price=price, discount=disc))
    return s


# ── Discount maths ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fixed_discount_is_judged_as_pct_of_qty_times_price(client, db):
    H, org_id = await _register(client)
    ana, ben, _ = await _team(client, H)
    for _ in range(3):
        await _line(db, org_id, by=ana, qty=1, price=100, disc=30, dtype="fixed")    # 30%
        await _line(db, org_id, by=ben, qty=2, price=100, disc=30, dtype="fixed")    # 15%: under 20
    await db.commit()
    f = _kinds(await _wd(db, org_id), "big_discounts")
    assert set(f) == {ana}
    assert f[ana]["evidence"][0]["pct"] == 30.0 and f[ana]["amount"] == 90


@pytest.mark.asyncio
async def test_pct_discount_amount_uses_qty(client, db):
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(3):
        await _line(db, org_id, by=ana, qty=3, price=100, disc=30)
    await db.commit()
    assert _kinds(await _wd(db, org_id), "big_discounts")[ana]["amount"] == 270


# ── Actor hook ──────────────────────────────────────────────────────────────────
async def _api_line(client, H, disc):
    cid = (await client.post("/api/clients", json={"name": "Jo"}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={"line_type": "labour", "kind": "service",
                          "description": "Fix", "qty": 1, "unit_price": 100, "discount": disc}, headers=H)
    assert r.status_code in (200, 201), r.text
    return oid


@pytest.mark.asyncio
async def test_patch_by_someone_else_reattributes_the_discount(client, db):
    from backend.models import OrderLine
    H, org_id = await _register(client)
    T = await _staff(client, H, PERMS)
    t_id = await _emp_id(client, T)
    oid = await _api_line(client, H, 0)                       # created with no discount
    ln = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().first()
    assert ln.discount_by_id is None
    lid = ln.id
    r = await client.patch(f"/api/orders/{oid}/lines/{lid}", json={"discount": 30}, headers=T)
    assert r.status_code == 200, r.text
    db.expire_all()
    ln = (await db.execute(select(OrderLine).where(OrderLine.id == lid))).scalars().first()
    assert ln.discount_by_id == t_id


@pytest.mark.asyncio
async def test_no_request_person_leaves_actor_empty(client, db):
    from backend.models import CashShift, OrderLine, Refund
    H, org_id = await _register(client)
    me = await _emp_id(client, H)
    o = await _line(db, org_id, by=None, disc=50)
    r = Refund(org_id=org_id, amount=10, account_id=await _account(db, org_id))
    s = CashShift(location_id=await _loc(db, org_id), opened_by_id=me, opening_float=0,
                  closed_at=datetime.utcnow(), closing_float=1, expected_cash=1)
    db.add_all([r, s])
    await db.flush()
    ln = (await db.execute(select(OrderLine).where(OrderLine.order_id == o.id))).scalars().first()
    assert ln.discount_by_id is None and r.created_by_id is None and s.closed_by_id is None
    await db.rollback()


@pytest.mark.asyncio
async def test_duplicating_a_job_does_not_pin_old_discounts_on_the_duplicator(client, db):
    """A copied discount stays with whoever gave it, not whoever pressed Duplicate."""
    from backend.models import OrderLine
    H, org_id = await _register(client)
    owner = await _emp_id(client, H)
    T = await _staff(client, H, PERMS)
    t_id = await _emp_id(client, T)
    oid = await _api_line(client, H, 30)
    r = await client.post(f"/api/orders/{oid}/duplicate", json={}, headers=T)
    assert r.status_code == 201, r.text
    new = (await db.execute(select(OrderLine).where(OrderLine.order_id == r.json()["id"]))).scalars().first()
    assert new.discount_by_id in (None, owner) and new.discount_by_id != t_id


# ── POS ─────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pos_line_discounts_go_to_the_seller_and_voided_sales_do_not_count(client, db):
    H, org_id = await _register(client)
    ana, ben, _ = await _team(client, H)
    for _ in range(3):
        await _sale(db, org_id, seller=ana, disc=25)
        await _sale(db, org_id, seller=ben, disc=50, voided=True)
    await db.commit()
    f = _kinds(await _wd(db, org_id), "big_discounts")
    assert set(f) == {ana} and f[ana]["amount"] == 75


@pytest.mark.asyncio
async def test_pos_whole_sale_discount_is_seen(client, db):
    """A discount on the whole counter sale counts, not only per-line ones."""
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(4):
        await _sale(db, org_id, seller=ana, whole_discount=50, price=100)       # 50% off the basket
    await db.commit()
    assert ana in _kinds(await _wd(db, org_id), "big_discounts")


@pytest.mark.asyncio
async def test_discount_given_recently_on_an_older_job_counts(client, db):
    """A discount given recently on an older job still counts."""
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(4):
        await _line(db, org_id, by=ana, disc=40, days=45)        # job opened 45 days ago, discounted now
    await db.commit()
    assert ana in _kinds(await _wd(db, org_id), "big_discounts")


# ── Cash ────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cash_short_with_no_recorded_closer_names_nobody(client, db):
    """Never pinned on whoever opened the drawer: only the person who counted it is named."""
    from backend.models import CashShift
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    db.add(CashShift(location_id=await _loc(db, org_id), opened_by_id=ana, closed_by_id=None, opening_float=0,
                     expected_cash=300, closing_float=200, closed_at=datetime.utcnow() - timedelta(hours=2)))
    await db.commit()
    shorts = [f for f in (await _wd(db, org_id))["flags"] if f["kind"] == "cash_short"]
    assert len(shorts) == 1 and shorts[0]["employee_id"] is None and shorts[0]["amount"] == 100


@pytest.mark.asyncio
async def test_owner_closed_drawer_is_never_flagged(client, db):
    from backend.models import CashShift
    H, org_id = await _register(client)
    owner = await _emp_id(client, H)
    db.add(CashShift(location_id=await _loc(db, org_id), opened_by_id=owner, closed_by_id=owner, opening_float=0,
                     expected_cash=300, closing_float=0, closed_at=datetime.utcnow() - timedelta(hours=2)))
    await db.commit()
    assert _kinds(await _wd(db, org_id), "cash_short") == {}


# ── Who is compared ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_owner_never_flagged_and_not_in_the_median(client, db):
    H, org_id = await _register(client)
    owner = await _emp_id(client, H)
    ana, ben = await _team(client, H, n=2)
    for _ in range(6):
        await _line(db, org_id, by=owner, disc=50)
    for _ in range(4):
        await _line(db, org_id, by=ana, disc=30)
    for _ in range(3):
        await _line(db, org_id, by=ben, disc=30)
    await db.commit()
    f = _kinds(await _wd(db, org_id), "big_discounts")
    # others for ana = [ben=3] -> median 3 -> 4 < 6: not flagged. If the owner counted (as 6 or 0) it would change.
    assert owner not in f and ana not in f


@pytest.mark.asyncio
async def test_inactive_people_are_left_out_of_flags_medians_and_scorecard(client, db):
    from backend.models import Employee
    H, org_id = await _register(client)
    ana, ben, gone = await _team(client, H)
    for _ in range(9):
        await _line(db, org_id, by=gone, disc=30)
    for _ in range(3):
        await _line(db, org_id, by=ana, disc=30)
    (await db.execute(select(Employee).where(Employee.id == gone))).scalar_one().active = False
    await db.commit()
    res = await _wd(db, org_id)
    f = _kinds(res, "big_discounts")
    assert gone not in f and f[ana]["others_median"] == 0          # median over [ben] only
    assert gone not in {r["employee_id"] for r in res["scorecard"]}


@pytest.mark.asyncio
async def test_single_staff_member_is_compared_with_zero(client, db):
    """Documents current behaviour: with no colleagues the median is 0, so 3 events flag."""
    H, org_id = await _register(client)
    (ana,) = await _team(client, H, n=1)
    for _ in range(3):
        await _line(db, org_id, by=ana, disc=25)
    await db.commit()
    assert _kinds(await _wd(db, org_id), "big_discounts")[ana]["others_median"] == 0


@pytest.mark.asyncio
async def test_tenant_isolation_with_shared_employee_ids_in_events(client, db):
    """Org B's events must not raise org A's medians (kills the tenant-filter mutation)."""
    H, org_a = await _register(client)
    HB, org_b = await _register(client)
    ana, ben = await _team(client, H, n=2)
    for _ in range(4):
        await _line(db, org_a, by=ana, disc=30)
    for _ in range(4):
        await _line(db, org_b, by=ben, disc=30)          # junk row: ben's id under another shop
    await db.commit()
    res = await _wd(db, org_a)
    f = _kinds(res, "big_discounts")
    assert ana in f and f[ana]["others_median"] == 0 and ben not in f
    b_owner = (await client.get("/api/auth/me", headers=HB)).json()["id"]
    assert b_owner not in {r["employee_id"] for r in res["scorecard"]}     # no other shop's people


# ── Review keys ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_reviewed_flag_stays_hidden_until_something_new_happens(client, db):
    """Reviewed once, it doesn't come back the next week for the same events; a new one brings it back."""
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(4):
        await _line(db, org_id, by=ana, disc=30, days=1)
    await db.commit()
    key = _kinds(await _wd(db, org_id), "big_discounts")[ana]["key"]
    await sense.review_flag(db, org_id, key)
    await db.commit()
    assert _kinds(await _wd(db, org_id), "big_discounts") == {}
    assert _kinds(await _wd(db, org_id, now=datetime.utcnow() + timedelta(days=8)), "big_discounts") == {}
    await _line(db, org_id, by=ana, disc=30, days=0)        # something new
    await db.commit()
    assert ana in _kinds(await _wd(db, org_id), "big_discounts")


# ── Scorecard ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scorecard_value_is_order_total(client, db):
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    await _job(db, org_id, assigned=ana, lines=[("A", 120, 0), ("B", 30, 0)])
    await db.commit()
    rows = {r["employee_id"]: r for r in (await _wd(db, org_id))["scorecard"]}
    assert rows[ana]["value"] == 150 and rows[ana]["jobs_closed"] == 1


@pytest.mark.asyncio
async def test_a_job_is_never_its_own_comeback(client, db):
    """A job is never its own comeback (e.g. imported or back-dated data)."""
    from backend.models import Device
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    dev = Device(org_id=org_id, brand="A", model="B")
    db.add(dev)
    await db.flush()
    now = datetime.utcnow()
    await _closed(db, org_id, assigned=ana, device_id=dev.id, created=now - timedelta(days=2),
                  closed=now - timedelta(days=5))                   # back-dated close
    await db.commit()
    rows = {r["employee_id"]: r for r in (await _wd(db, org_id))["scorecard"]}
    assert rows[ana]["comebacks"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("after_days,expected", [(29, 1), (5, 1)])
async def test_comeback_window_is_30_days(client, db, after_days, expected):
    from backend.models import Device
    H, org_id = await _register(client)
    ana, ben, _ = await _team(client, H)
    dev = Device(org_id=org_id, brand="A", model="B")
    db.add(dev)
    await db.flush()
    now = datetime.utcnow()
    closed = now - timedelta(days=29, hours=12) if after_days < 30 else now - timedelta(days=25)
    await _closed(db, org_id, assigned=ana, device_id=dev.id, created=closed - timedelta(days=1), closed=closed)
    if after_days < 30:
        await _closed(db, org_id, assigned=ben, device_id=dev.id, created=closed + timedelta(days=after_days), closed=None)
    else:                                          # job closed 25 days ago, comeback 31 days later is in the future: use an older job
        old = now - timedelta(days=29, hours=20)
        await _closed(db, org_id, assigned=ben, device_id=dev.id, created=old - timedelta(days=1), closed=old)
        # ben's job is the comeback candidate for nothing; ana's closed 25d ago, nothing after it
    await db.commit()
    rows = {r["employee_id"]: r for r in (await _wd(db, org_id))["scorecard"]}
    assert rows[ana]["comebacks"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("estimate,archived", [(True, False), (False, True)])
async def test_estimate_or_archived_later_job_is_not_a_comeback(client, db, estimate, archived):
    """Estimates and deleted (archived) jobs don't count as comebacks."""
    from backend.models import Device
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    dev = Device(org_id=org_id, brand="A", model="B")
    db.add(dev)
    await db.flush()
    now = datetime.utcnow()
    await _closed(db, org_id, assigned=ana, device_id=dev.id, created=now - timedelta(days=12),
                  closed=now - timedelta(days=10))
    await _closed(db, org_id, assigned=None, device_id=dev.id, created=now - timedelta(days=5), closed=None,
                  is_estimate=estimate, archived=archived)
    await db.commit()
    rows = {r["employee_id"]: r for r in (await _wd(db, org_id))["scorecard"]}
    assert rows[ana]["comebacks"] == 0


# ── Rule coverage the sprint-3 suite misses (mutation survivors) ──────────────────
@pytest.mark.asyncio
async def test_voided_refunds_are_not_counted_as_refunds(client, db):
    from backend.models import Refund
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    acct = await _account(db, org_id)
    now = datetime.utcnow()
    for i in range(4):
        db.add(Refund(org_id=org_id, amount=50, account_id=acct, created_by_id=ana,
                      created_at=now - timedelta(days=i), voided_at=now))
    await db.commit()
    res = await _wd(db, org_id)
    assert _kinds(res, "refunds") == {}
    assert {r["employee_id"]: r for r in res["scorecard"]}[ana]["refunds"] == 0


@pytest.mark.asyncio
async def test_needs_twice_the_median_not_just_the_minimum(client, db):
    H, org_id = await _register(client)
    ana, ben, cy = await _team(client, H)
    for who, n in ((ana, 5), (ben, 3), (cy, 3)):          # 5 < 2 x 3
        for _ in range(n):
            await _line(db, org_id, by=who, disc=30)
    await db.commit()
    assert _kinds(await _wd(db, org_id), "big_discounts") == {}
