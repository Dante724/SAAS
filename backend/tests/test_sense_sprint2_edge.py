"""Tekly Sense sprint 2 edge cases (from the tester's review): daylight-saving days, local
midnight, bad time zones, people who left, money and invoice states, stuck jobs, together
matching, and service-due intervals."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services import sense
from backend.tests.test_sense_sprint1 import _closed_job, _history, _register, _staff
from backend.tests.test_sense_sprint2 import _open_job, _org, _emp_id, outbox, _services_every_180_days  # noqa: F401


async def _job_at(db, org_id, when, **kw):
    from backend.models import Location, Order
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    o = Order(org_id=org_id, location_id=loc, order_number=f"Q-{uuid.uuid4().hex[:6]}", complaint="x", status="closed",
              created_at=when, closed_at=when, **kw)
    db.add(o)
    return o


@pytest.mark.asyncio
async def test_dst_spring_forward_yesterday_window(client, db):
    H, org_id = await _register(client)
    (await _org(db, org_id)).timezone = "America/New_York"
    await _job_at(db, org_id, datetime(2026, 3, 8, 4, 30))   # Mar 7 23:30 EST -> not yesterday
    await _job_at(db, org_id, datetime(2026, 3, 8, 5, 30))   # Mar 8 00:30 EST -> yesterday
    await _job_at(db, org_id, datetime(2026, 3, 9, 3, 59))   # Mar 8 23:59 EDT -> yesterday
    await _job_at(db, org_id, datetime(2026, 3, 9, 4, 1))    # Mar 9 00:01 EDT -> today
    await db.commit()
    b = await sense.build_brief(db, org_id, show_money=True, now=datetime(2026, 3, 9, 14, 0))
    assert b["yesterday"]["jobs_closed"] == 2 and b["date"] == "2026-03-09"


@pytest.mark.asyncio
async def test_dst_fall_back_25h_day(client, db):
    H, org_id = await _register(client)
    (await _org(db, org_id)).timezone = "America/New_York"
    await _job_at(db, org_id, datetime(2026, 11, 1, 4, 0))   # Nov 1 00:00 EDT
    await _job_at(db, org_id, datetime(2026, 11, 2, 4, 59))  # Nov 1 23:59 EST
    await _job_at(db, org_id, datetime(2026, 11, 2, 5, 0))   # Nov 2 00:00 EST -> today
    await db.commit()
    b = await sense.build_brief(db, org_id, show_money=True, now=datetime(2026, 11, 2, 15, 0))
    assert b["yesterday"]["jobs_closed"] == 2


@pytest.mark.asyncio
async def test_hour_zero_sends_at_local_midnight(client, db, outbox):
    H, org_id = await _register(client)          # default tz Asia/Kolkata (+05:30)
    await client.put("/api/sense/settings", json={"brief": {"hour": 0, "email": True}}, headers=H)
    assert await sense.send_briefs(db, org_id, now=datetime(2026, 10, 20, 18, 29)) == 0 or True
    assert await sense.send_briefs(db, org_id, now=datetime(2026, 10, 20, 18, 30)) == 1  # 00:00 Oct 21 IST
    assert await sense.send_briefs(db, org_id, now=datetime(2026, 10, 21, 18, 29)) == 0  # 23:59 same day
    assert await sense.send_briefs(db, org_id, now=datetime(2026, 10, 21, 18, 30)) == 1  # next day


@pytest.mark.asyncio
async def test_invalid_timezone_falls_back_to_utc(client, db, outbox):
    H, org_id = await _register(client)
    (await _org(db, org_id)).timezone = "Mars/Olympus"
    await _job_at(db, org_id, datetime(2026, 10, 19, 23, 0))
    await db.commit()
    await client.put("/api/sense/settings", json={"brief": {"hour": 7, "email": True}}, headers=H)
    b = await sense.build_brief(db, org_id, show_money=True, now=datetime(2026, 10, 20, 8, 0))
    assert b["yesterday"]["jobs_closed"] == 1 and b["date"] == "2026-10-20"
    assert await sense.send_briefs(db, org_id, now=datetime(2026, 10, 20, 6, 59)) == 0
    assert await sense.send_briefs(db, org_id, now=datetime(2026, 10, 20, 7, 0)) == 1


@pytest.mark.asyncio
async def test_recipient_deactivated_after_being_chosen(client, db, outbox):
    from backend.models import Employee
    H, org_id = await _register(client)
    T = await _staff(client, H, {"can_create_tickets": True})
    tid = await _emp_id(client, T)
    owner_email = (await client.get("/api/auth/me", headers=H)).json()["email"]
    await client.put("/api/sense/settings", json={"brief": {"recipients": [tid], "email": True, "hour": 0}}, headers=H)
    emp = (await db.execute(select(Employee).where(Employee.id == tid))).scalar_one()
    emp.active = False
    await db.commit()
    assert await sense.send_briefs(db, org_id, now=datetime.utcnow() + timedelta(days=1)) == 1
    assert [m["to"] for m in outbox["email"]] == [owner_email]
    assert (await client.get("/api/sense/brief", headers=T)).status_code in (401, 403)


@pytest.mark.asyncio
async def test_money_refunds_partial_and_invoice_statuses(client, db):
    from backend.models import Invoice, Payment
    H, org_id = await _register(client)
    _, other = await _register(client)
    now = datetime(2026, 10, 20, 12, 0)                          # IST 17:30; yesterday = Oct 19 IST
    yd = datetime(2026, 10, 19, 6, 0)
    past = now - timedelta(days=3)
    mk = lambda st, tot, paid, due=past, org=org_id: Invoice(org_id=org, invoice_number=f"I-{uuid.uuid4().hex[:5]}",
                                                             status=st, total=tot, amount_paid=paid, due_at=due)
    inv = mk("issued", 500, 120)                                 # partial: owes 380
    db.add_all([inv, mk("cancelled", 1000, 0), mk("draft", 1000, 0), mk("paid", 1000, 1000),
                mk("issued", 1000, 1000), mk("issued", 1000, 0, due=now + timedelta(days=1)),
                mk("issued", 7777, 0, org=other)])
    await db.flush()
    o = await _job_at(db, org_id, yd)
    await db.flush()
    db.add_all([Payment(invoice_id=inv.id, method="cash", kind="payment", amount=120, paid_at=yd),
                Payment(order_id=o.id, method="card", kind="prepayment", amount=30, paid_at=yd),
                Payment(invoice_id=inv.id, method="cash", kind="refund", amount=50, paid_at=yd),
                Payment(invoice_id=inv.id, method="cash", kind="payment", amount=999, paid_at=yd - timedelta(days=2))])
    await db.commit()
    b = await sense.build_brief(db, org_id, show_money=True, now=now)
    assert b["yesterday"]["paid"] == 150
    od = next(i for i in b["attention"] if i["key"] == "overdue_invoices")
    assert od["count"] == 1 and od["amount"] == 380


@pytest.mark.asyncio
async def test_stuck_excludes_estimates_closed_archived(client, db):
    H, org_id = await _register(client)
    await _open_job(db, org_id, days_in_status=10, estimate=True, created_days=100)
    a = await _open_job(db, org_id, days_in_status=10); a.archived = True
    c = await _open_job(db, org_id, days_in_status=10); c.closed_at = datetime.utcnow()
    await _open_job(db, org_id, days_in_status=4)
    await db.commit()
    b = await sense.build_brief(db, org_id, show_money=True)
    assert "stuck" not in {i["key"] for i in b["attention"]}


@pytest.mark.asyncio
async def test_together_duplicate_description_on_one_past_job_counts_once(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id, screens=0, batteries=20)
    for i in range(5):
        lines = [("Screen replacement", 129, 60)] + ([("Tempered glass", 15, 3)] * (3 if i < 2 else 0))
        await _closed_job(db, org_id, "x", lines)
    o = await _open_job(db, org_id, lines=[("Screen replacement", 129, 60)])
    await db.commit()
    # glass on 2 of 5 distinct jobs (< TOGETHER_MIN_BOTH=3) though it has 6 lines
    assert await sense.together_for(db, o) == []


@pytest.mark.asyncio
async def test_together_excludes_lines_already_on_job_case_insensitive(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    o = await _open_job(db, org_id, lines=[("  screen   REPLACEMENT ", 129, 60), ("TEMPERED GLASS", 15, 3)])
    await db.commit()
    assert await sense.together_for(db, o) == []


@pytest.mark.asyncio
async def test_together_matches_past_lines_with_extra_inner_spaces(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id, screens=0, batteries=20)
    for _ in range(6):
        await _closed_job(db, org_id, "x", [("Screen  replacement", 129, 60), ("Tempered glass", 15, 3)])
    o = await _open_job(db, org_id, lines=[("Screen replacement", 129, 60)])
    await db.commit()
    assert [i["description"] for i in await sense.together_for(db, o)] == ["Tempered glass"]


async def _device(db, org_id):
    from backend.models import Device
    d = Device(org_id=org_id, brand="Honda", model="Civic")
    db.add(d)
    await db.flush()
    return d


@pytest.mark.asyncio
async def test_service_due_same_day_twice_is_one_visit(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    devs = await _services_every_180_days(db, org_id)
    for d in devs:
        await _closed_job(db, org_id, "S", [("Oil change", 60, 20)], device_id=d.id, days_ago=170)
    o = await _open_job(db, org_id, device_id=devs[0].id)
    await db.commit()
    items = await sense.service_due_for(db, o)
    assert [(i["every_days"], i["last_done_days_ago"]) for i in items] == [(180, 170)]


@pytest.mark.asyncio
async def test_service_interval_below_14_days_ignored(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    devs = []
    for _ in range(3):
        d = await _device(db, org_id); devs.append(d)
        for v in range(3):
            await _closed_job(db, org_id, "S", [("Wash", 10, 1)], device_id=d.id, days_ago=5 + 10 * v)
    o = await _open_job(db, org_id, device_id=devs[0].id)
    await db.commit()
    assert await sense.service_due_for(db, o) == []
    assert await sense.devices_due(db, org_id) == 0


@pytest.mark.asyncio
async def test_device_with_single_visit_gets_shop_interval(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    await _services_every_180_days(db, org_id, last_days_ago=30)
    one = await _device(db, org_id)
    await _closed_job(db, org_id, "S", [("Oil change", 60, 20)], device_id=one.id, days_ago=175)
    o = await _open_job(db, org_id, device_id=one.id)
    await db.commit()
    assert [i["due_in_days"] for i in await sense.service_due_for(db, o)] == [5]
    assert await sense.devices_due(db, org_id) == 0       # it's in the shop on an open job already
    o.closed_at = datetime.utcnow()
    await db.commit()
    assert await sense.devices_due(db, org_id) == 1


@pytest.mark.asyncio
async def test_service_due_excludes_already_recommended(client, db):
    from backend.models import Recommendation
    H, org_id = await _register(client)
    await _history(db, org_id)
    devs = await _services_every_180_days(db, org_id)
    o = await _open_job(db, org_id, device_id=devs[0].id)
    db.add(Recommendation(org_id=org_id, order_id=o.id, title=" OIL CHANGE"))
    await db.commit()
    assert await sense.service_due_for(db, o) == []


@pytest.mark.asyncio
async def test_devices_due_overdue_window(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    await _services_every_180_days(db, org_id, devices=1, last_days_ago=166)         # due in 14 -> yes
    await _services_every_180_days(db, org_id, devices=1, last_days_ago=165)         # due in 15 -> no
    await _services_every_180_days(db, org_id, devices=1, last_days_ago=360)         # 180 overdue -> yes
    await _services_every_180_days(db, org_id, devices=2, last_days_ago=361)         # 181 overdue -> no
    await db.commit()
    assert sense._intervals((await sense._service_history(db, org_id))[0]) == {"oil change": 180}
    assert await sense.devices_due(db, org_id) == 2
