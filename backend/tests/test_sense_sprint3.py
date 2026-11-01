"""Tekly Sense sprint 3: Watchdog and the team scorecard, for the shop owner only.

Fair by construction: every flag needs a minimum count and twice what others usually do,
shows the median it was compared with and the records behind it, says "unusual", and does
nothing on its own. Who gave a discount, made a refund or closed a drawer is now recorded
for every code path.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services import sense
from backend.tests.test_sense_sprint1 import _register, _staff
from backend.tests.test_sense_sprint2 import _emp_id


async def _account(db, org_id):
    from backend.models import Account
    a = Account(org_id=org_id, name=f"Till {uuid.uuid4().hex[:4]}")
    db.add(a)
    await db.flush()
    return a.id


async def _loc(db, org_id):
    from backend.models import Location
    return (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()


async def _job(db, org_id, *, by=None, lines=(), assigned=None, closed_days=2, device_id=None, created_days=None):
    """lines: (description, price, discount_pct)"""
    from backend.models import Order, OrderLine
    when = datetime.utcnow() - timedelta(days=closed_days)
    o = Order(org_id=org_id, order_number=f"W-{uuid.uuid4().hex[:6]}", location_id=await _loc(db, org_id),
              complaint="x", status="closed", created_at=datetime.utcnow() - timedelta(days=created_days or closed_days),
              closed_at=when, assigned_to_id=assigned, device_id=device_id, total=sum(p for _, p, _ in lines))
    db.add(o)
    await db.flush()
    for d, p, disc in lines:
        db.add(OrderLine(order_id=o.id, line_type="labour", kind="service", description=d, qty=1, unit_price=p,
                         discount=disc, discount_type="pct", discount_by_id=by if disc else None,
                         total=p * (1 - disc / 100)))
    return o


async def _team(client, H, n=3):
    out = []
    for _ in range(n):
        T = await _staff(client, H, {"can_create_tickets": True, "can_set_discount": True})
        out.append(await _emp_id(client, T))
    return out


# ── Who did it ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_discount_records_who_gave_it(client, db):
    from backend.models import OrderLine
    H, org_id = await _register(client)
    me = await _emp_id(client, H)
    cid = (await client.post("/api/clients", json={"name": "Jo"}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=H)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json={"line_type": "labour", "kind": "service",
                          "description": "Fix", "qty": 1, "unit_price": 100, "discount": 30}, headers=H)
    assert r.status_code in (200, 201), r.text
    ln = (await db.execute(select(OrderLine).where(OrderLine.order_id == oid))).scalars().first()
    assert ln.discount_by_id == me


@pytest.mark.asyncio
async def test_refunds_and_drawer_closes_record_who_did_them(client, db):
    from backend.models import CashShift, Organisation, Refund
    H, org_id = await _register(client)
    me = await _emp_id(client, H)
    db.info["actor_id"] = me                 # what get_current_employee does for a request
    try:
        r = Refund(org_id=org_id, amount=10, account_id=await _account(db, org_id))
        s = CashShift(location_id=await _loc(db, org_id), opened_by_id=me, opening_float=0)
        db.add_all([r, s])
        await db.flush()
        assert r.created_by_id == me and s.closed_by_id is None
        s.closed_at, s.closing_float, s.expected_cash = datetime.utcnow(), 90, 100
        await db.flush()
        assert s.closed_by_id == me
    finally:
        db.info.pop("actor_id", None)
        await db.rollback()


# ── Watchdog flags ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unusual_discounts_are_flagged_with_the_evidence(client, db):
    H, org_id = await _register(client)
    ana, ben, cy = await _team(client, H)
    for _ in range(5):
        await _job(db, org_id, by=ana, lines=[("Screen", 200, 30)])
    await _job(db, org_id, by=ben, lines=[("Screen", 200, 25)])
    await _job(db, org_id, by=cy, lines=[("Screen", 200, 5)])
    await db.commit()

    r = await client.get("/api/sense/watchdog", headers=H)
    assert r.status_code == 200, r.text
    flags = [f for f in r.json()["flags"] if f["kind"] == "big_discounts"]
    assert len(flags) == 1
    f = flags[0]
    assert f["employee_id"] == ana and f["count"] == 5 and f["others_median"] == 0.5
    assert f["amount"] == 300 and len(f["evidence"]) == 5 and "unusual" in f["title"].lower()
    assert "suspicious" not in r.text.lower()


@pytest.mark.asyncio
async def test_too_little_to_go_on_flags_nothing(client, db):
    H, org_id = await _register(client)
    ana, ben, _ = await _team(client, H)
    for _ in range(2):                                    # under the minimum of 3
        await _job(db, org_id, by=ana, lines=[("Screen", 200, 40)])
    await db.commit()
    assert [f for f in (await client.get("/api/sense/watchdog", headers=H)).json()["flags"]
            if f["kind"] == "big_discounts"] == []


@pytest.mark.asyncio
async def test_refunds_voids_and_cash_shortfalls_are_flagged(client, db):
    from backend.models import CashShift, Refund, Sale
    H, org_id = await _register(client)
    ana, ben, cy = await _team(client, H)
    now = datetime.utcnow()
    acct = await _account(db, org_id)
    for i in range(4):
        db.add(Refund(org_id=org_id, amount=50, account_id=acct, created_by_id=ana, created_at=now - timedelta(days=i)))
        db.add(Sale(org_id=org_id, location_id=await _loc(db, org_id), sale_number=f"S-{i}", seller_id=ben, total=80,
                    voided_at=now - timedelta(days=i), voided_by_id=ben, created_at=now - timedelta(days=i)))
    db.add(CashShift(location_id=await _loc(db, org_id), opened_by_id=cy, closed_by_id=cy, opening_float=0,
                     expected_cash=500, closing_float=440, opened_at=now - timedelta(days=1, hours=8),
                     closed_at=now - timedelta(days=1)))
    db.add(CashShift(location_id=await _loc(db, org_id), opened_by_id=ben, closed_by_id=ben, opening_float=0,
                     expected_cash=500, closing_float=495, opened_at=now - timedelta(days=2, hours=8),
                     closed_at=now - timedelta(days=2)))                       # inside the tolerance
    await db.commit()
    flags = (await client.get("/api/sense/watchdog", headers=H)).json()["flags"]
    kinds = {(f["kind"], f["employee_id"]) for f in flags}
    assert ("refunds", ana) in kinds and ("voids", ben) in kinds and ("cash_short", cy) in kinds
    assert ("cash_short", ben) not in kinds
    short = next(f for f in flags if f["kind"] == "cash_short")
    assert short["amount"] == 60


@pytest.mark.asyncio
async def test_only_the_owner_sees_the_watchdog(client, db):
    H, org_id = await _register(client)
    boss_like = await _staff(client, H, {"can_manage_settings": True, "can_view_finance": True,
                                         "can_view_reports": True, "can_manage_employees": True})
    assert (await client.get("/api/sense/watchdog", headers=boss_like)).status_code == 403
    assert (await client.post("/api/sense/watchdog/review", json={"key": "refunds:abc:2026-10-20"}, headers=boss_like)).status_code == 403
    HB, _ = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(5):
        await _job(db, org_id, by=ana, lines=[("Screen", 200, 30)])
    await db.commit()
    assert (await client.get("/api/sense/watchdog", headers=HB)).json()["flags"] == []


@pytest.mark.asyncio
async def test_reviewing_a_flag_hides_it_and_is_audited(client, db):
    from backend.models import AuditLog
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(5):
        await _job(db, org_id, by=ana, lines=[("Screen", 200, 30)])
    await db.commit()
    f = (await client.get("/api/sense/watchdog", headers=H)).json()["flags"][0]
    r = await client.post("/api/sense/watchdog/review", json={"key": f["key"], "note": "Agreed with me"}, headers=H)
    assert r.status_code == 200
    assert (await client.get("/api/sense/watchdog", headers=H)).json()["flags"] == []
    assert (await db.execute(select(AuditLog).where(AuditLog.org_id == org_id,
                                                    AuditLog.action == "watchdog.flag.review"))).scalars().first()


@pytest.mark.asyncio
async def test_switching_watchdog_off_stops_it(client, db):
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(5):
        await _job(db, org_id, by=ana, lines=[("Screen", 200, 30)])
    await db.commit()
    await client.put("/api/sense/settings", json={"features": {"watchdog": False}}, headers=H)
    r = (await client.get("/api/sense/watchdog", headers=H)).json()
    assert r["enabled"] is False and r["flags"] == [] and r["scorecard"] == []


@pytest.mark.asyncio
async def test_the_owner_sets_the_thresholds(client, db):
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    for _ in range(4):
        await _job(db, org_id, by=ana, lines=[("Screen", 200, 15)])       # under the default 20%
    await db.commit()
    assert (await client.get("/api/sense/watchdog", headers=H)).json()["flags"] == []
    r = await client.put("/api/sense/settings", json={"watchdog": {"discount_pct": 10}}, headers=H)
    assert r.status_code == 200 and r.json()["watchdog"]["discount_pct"] == 10
    assert [f["kind"] for f in (await client.get("/api/sense/watchdog", headers=H)).json()["flags"]] == ["big_discounts"]


# ── Team scorecard ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_scorecard_counts_jobs_value_and_comebacks(client, db):
    from backend.models import Device
    H, org_id = await _register(client)
    ana, ben, _ = await _team(client, H)
    dev = Device(org_id=org_id, brand="A", model="B")
    db.add(dev)
    await db.flush()
    await _job(db, org_id, assigned=ana, lines=[("Fix", 100, 0)], closed_days=20, device_id=dev.id)
    await _job(db, org_id, assigned=ben, lines=[("Fix again", 50, 0)], closed_days=3, created_days=10, device_id=dev.id)
    await _job(db, org_id, assigned=ana, lines=[("Other", 200, 0)], closed_days=5)
    await db.commit()
    rows = {r["employee_id"]: r for r in (await client.get("/api/sense/watchdog", headers=H)).json()["scorecard"]}
    assert rows[ana]["jobs_closed"] == 2 and rows[ana]["value"] == 300 and rows[ana]["comebacks"] == 1
    assert rows[ben]["jobs_closed"] == 1 and rows[ben]["comebacks"] == 0


# ── Morning Brief ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_only_the_owners_brief_mentions_the_watchdog(client, db):
    H, org_id = await _register(client)
    T = await _staff(client, H, {"can_create_tickets": True})
    ana, _, _ = await _team(client, H)
    for _ in range(5):
        await _job(db, org_id, by=ana, lines=[("Screen", 200, 30)])
    await db.commit()
    await client.put("/api/sense/settings", json={"brief": {"recipients": [await _emp_id(client, T)]}}, headers=H)
    owner_keys = [i["key"] for i in (await client.get("/api/sense/brief", headers=H)).json()["attention"]]
    staff_keys = [i["key"] for i in (await client.get("/api/sense/brief", headers=T)).json()["attention"]]
    assert "watchdog" in owner_keys and "watchdog" not in staff_keys


@pytest.mark.asyncio
async def test_copying_a_job_keeps_the_discount_with_whoever_gave_it(client, db):
    from backend.models import OrderLine
    H, org_id = await _register(client)
    me = await _emp_id(client, H)
    T = await _staff(client, H, {"can_create_tickets": True})
    tid = await _emp_id(client, T)
    cid = (await client.post("/api/clients", json={"name": "Jo"}, headers=H)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "x"}, headers=H)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={"line_type": "labour", "kind": "service",
                      "description": "Fix", "qty": 1, "unit_price": 100, "discount": 30}, headers=H)
    r = await client.post(f"/api/orders/{oid}/duplicate", headers=T)
    assert r.status_code == 201, r.text
    new_id = r.json().get("id") or r.json().get("order", {}).get("id")
    ln = (await db.execute(select(OrderLine).where(OrderLine.order_id == new_id))).scalars().first()
    assert ln.discount_by_id == me and ln.discount_by_id != tid


@pytest.mark.asyncio
async def test_a_short_drawer_with_no_recorded_closer_names_nobody(client, db):
    from backend.models import CashShift
    H, org_id = await _register(client)
    ana, _, _ = await _team(client, H)
    now = datetime.utcnow()
    db.add(CashShift(location_id=await _loc(db, org_id), opened_by_id=ana, closed_by_id=None, opening_float=0,
                     expected_cash=300, closing_float=200, opened_at=now - timedelta(hours=9), closed_at=now - timedelta(hours=1)))
    await db.commit()
    f = next(f for f in (await client.get("/api/sense/watchdog", headers=H)).json()["flags"] if f["kind"] == "cash_short")
    assert f["employee_id"] is None and "wasn't recorded" in f["title"] and f["amount"] == 100


@pytest.mark.asyncio
async def test_staff_cannot_see_watchdog_settings(client):
    H, _ = await _register(client)
    T = await _staff(client, H, {"can_create_tickets": True})
    s = (await client.get("/api/sense/settings", headers=T)).json()
    assert "watchdog" not in s and "watchdog" not in s["features"] and "brief" not in s
    assert "watchdog" in (await client.get("/api/sense/settings", headers=H)).json()
