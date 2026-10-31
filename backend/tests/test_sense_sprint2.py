"""Tekly Sense sprint 2: the Morning Brief, "usually added together", and service due.

All of it comes from the shop's own data. The brief goes only to the owner and the people
the owner picks, money follows each reader's permissions, and the daily send happens once,
after the chosen hour in the shop's time zone, with nothing private in the push text.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text

from backend.services import sense
from backend.tests.test_sense_sprint1 import _closed_job, _history, _register, _staff


async def _org(db, org_id):
    from backend.models import Organisation
    return (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()


async def _emp_id(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()["id"]


async def _open_job(db, org_id, *, days_in_status=0, estimate=False, created_days=0, device_id=None, lines=()):
    from backend.models import Location, Order, OrderLine
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    now = datetime.utcnow()
    o = Order(org_id=org_id, order_number=f"O-{uuid.uuid4().hex[:6]}", location_id=loc, complaint="x",
              status="new", is_estimate=estimate, created_at=now - timedelta(days=created_days),
              status_entered_at=now - timedelta(days=days_in_status), device_id=device_id)
    db.add(o)
    await db.flush()
    for d, p, c in lines:
        db.add(OrderLine(order_id=o.id, line_type="labour", kind="service", description=d, qty=1,
                         unit_price=p, cost_price=c, total=p))
    return o


# ── Morning Brief: what it says and who sees it ─────────────────────────────────
@pytest.mark.asyncio
async def test_the_brief_counts_what_needs_the_owner(client, db):
    from backend.models import Invoice, Order
    H, org_id = await _register(client)
    yday = datetime.utcnow() - timedelta(days=1)
    j = await _closed_job(db, org_id, "Screen", [("Screen replacement", 100, 40)], days_ago=1)
    j.closed_at = yday
    await _open_job(db, org_id, days_in_status=7)                    # stuck
    await _open_job(db, org_id, estimate=True, created_days=3)       # cold quote
    db.add(Invoice(org_id=org_id, invoice_number=f"I-{uuid.uuid4().hex[:4]}", status="issued",
                   total=250, amount_paid=50, due_at=datetime.utcnow() - timedelta(days=3)))
    await db.commit()

    r = await client.get("/api/sense/brief", headers=H)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["yesterday"]["jobs_closed"] == 1
    keys = {i["key"]: i for i in b["attention"]}
    assert keys["stuck"]["count"] == 1 and keys["cold_quotes"]["count"] == 1
    assert keys["overdue_invoices"]["count"] == 1 and keys["overdue_invoices"]["amount"] == 200


@pytest.mark.asyncio
async def test_only_chosen_people_see_the_brief_and_money_follows_their_rights(client, db):
    from backend.models import Invoice
    H, org_id = await _register(client)
    db.add(Invoice(org_id=org_id, invoice_number="I-1", status="issued", total=300, amount_paid=0,
                   due_at=datetime.utcnow() - timedelta(days=2)))
    await db.commit()
    T = await _staff(client, H, {"can_view_finance": False, "can_create_tickets": True})
    assert (await client.get("/api/sense/brief", headers=T)).status_code == 403

    tid = await _emp_id(client, T)
    r = await client.put("/api/sense/settings", json={"brief": {"recipients": [tid]}}, headers=H)
    assert r.status_code == 200 and r.json()["brief"]["recipients"] == [tid]
    b = (await client.get("/api/sense/brief", headers=T)).json()
    inv = next(i for i in b["attention"] if i["key"] == "overdue_invoices")
    assert inv["count"] == 1 and "amount" not in inv and "paid" not in b["yesterday"]


@pytest.mark.asyncio
async def test_brief_recipients_must_work_in_this_shop(client):
    H, _ = await _register(client)
    HB, _ = await _register(client)
    stranger = await _emp_id(client, HB)
    r = await client.put("/api/sense/settings", json={"brief": {"recipients": [stranger]}}, headers=H)
    assert r.status_code == 422


# ── Morning Brief: the daily send ───────────────────────────────────────────────
@pytest.fixture
def outbox(monkeypatch):
    sent = {"email": [], "push": []}

    async def fake_email(db, org_id, *, recipient, subject, body):
        sent["email"].append({"to": recipient, "subject": subject, "body": body})

    async def fake_push(db, *, org_id, employee_id, kind, title, body="", deep_link=None, **kw):
        sent["push"].append({"to": employee_id, "title": title, "body": body})
    monkeypatch.setattr(sense, "_send_email", fake_email)
    monkeypatch.setattr(sense, "_send_push", fake_push)
    return sent


@pytest.mark.asyncio
async def test_the_brief_goes_out_once_after_the_hour_in_the_shops_time_zone(client, db, outbox):
    from backend.models import Invoice
    H, org_id = await _register(client)
    org = await _org(db, org_id)
    org.timezone = "America/New_York"
    db.add(Invoice(org_id=org_id, invoice_number="I-9", status="issued", total=999, amount_paid=0,
                   due_at=datetime(2026, 10, 18, 12, 0)))     # fixed, so the test doesn't age
    await db.commit()
    await client.put("/api/sense/settings", json={"brief": {"hour": 7, "email": True, "push": True}}, headers=H)

    six_am_ny = datetime(2026, 10, 20, 10, 0)          # 06:00 in New York (UTC-4)
    seven_am_ny = datetime(2026, 10, 20, 11, 5)        # 07:05 in New York
    assert await sense.send_briefs(db, org_id, now=six_am_ny) == 0
    assert outbox["email"] == [] and outbox["push"] == []

    assert await sense.send_briefs(db, org_id, now=seven_am_ny) == 1
    assert len(outbox["email"]) == 1 and len(outbox["push"]) == 1
    assert outbox["push"][0]["title"] == "Your morning brief is ready" and "999" not in str(outbox["push"])
    assert "999" in outbox["email"][0]["body"]          # the owner can see money

    assert await sense.send_briefs(db, org_id, now=seven_am_ny + timedelta(hours=3)) == 0   # not twice
    assert len(outbox["email"]) == 1


@pytest.mark.asyncio
async def test_no_brief_is_sent_until_the_owner_turns_a_channel_on(client, db, outbox):
    H, org_id = await _register(client)
    assert await sense.send_briefs(db, org_id, now=datetime.utcnow() + timedelta(hours=24)) == 0
    await client.put("/api/sense/settings", json={"brief": {"email": True}, "features": {"morning_brief": False}}, headers=H)
    assert await sense.send_briefs(db, org_id, now=datetime.utcnow() + timedelta(hours=24)) == 0
    assert outbox["email"] == []


# ── Usually added together ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_lines_usually_added_together_are_suggested(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)            # 12 jobs with Screen + Tempered glass, 8 with Battery
    o = await _open_job(db, org_id, lines=[("Screen replacement", 129, 60)])
    await db.commit()
    r = await client.get(f"/api/sense/orders/{o.id}/together", headers=H)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["description"] for i in items] == ["Tempered glass"]
    assert items[0]["unit_price"] == 15 and items[0]["why"] == {"with": "Screen replacement", "together": 12, "of": 12}

    HB, org_b = await _register(client)
    await _history(db, org_b, screens=0, batteries=20)
    ob = await _open_job(db, org_b, lines=[("Screen replacement", 129, 60)])
    await db.commit()
    assert (await client.get(f"/api/sense/orders/{ob.id}/together", headers=HB)).json()["items"] == []
    assert (await client.get(f"/api/sense/orders/{o.id}/together", headers=HB)).status_code == 404


@pytest.mark.asyncio
async def test_together_needs_the_suggestions_permission(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    o = await _open_job(db, org_id, lines=[("Screen replacement", 129, 60)])
    await db.commit()
    T = await _staff(client, H, {"can_create_tickets": True})
    assert (await client.get(f"/api/sense/orders/{o.id}/together", headers=T)).status_code == 403


# ── Service due ─────────────────────────────────────────────────────────────────
async def _services_every_180_days(db, org_id, devices=3, visits=3, last_days_ago=170):
    """Each device had an oil change every 180 days; the latest `last_days_ago` days ago."""
    from backend.models import Device
    out = []
    for _ in range(devices):
        dev = Device(org_id=org_id, brand="Honda", model="Civic")
        db.add(dev)
        await db.flush()
        for v in range(visits):
            await _closed_job(db, org_id, "Service", [("Oil change", 60, 20)], device_id=dev.id,
                              days_ago=last_days_ago + 180 * v)
        out.append(dev)
    return out


@pytest.mark.asyncio
async def test_service_due_is_learned_from_how_often_the_shop_repeats_it(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    devs = await _services_every_180_days(db, org_id)
    o = await _open_job(db, org_id, device_id=devs[0].id)
    await db.commit()
    r = await client.get(f"/api/sense/orders/{o.id}/service-due", headers=H)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["description"] for i in items] == ["Oil change"]
    assert items[0]["every_days"] == 180 and items[0]["last_done_days_ago"] == 170 and items[0]["unit_price"] == 60

    b = (await client.get("/api/sense/brief", headers=H)).json()
    # three are due; the one in the shop on this open job is already being looked after
    assert next(i for i in b["attention"] if i["key"] == "service_due")["count"] == 2


@pytest.mark.asyncio
async def test_service_not_due_yet_or_already_on_the_job_is_not_offered(client, db):
    H, org_id = await _register(client)
    await _history(db, org_id)
    fresh = await _services_every_180_days(db, org_id, last_days_ago=30)
    o = await _open_job(db, org_id, device_id=fresh[0].id)
    await db.commit()
    assert (await client.get(f"/api/sense/orders/{o.id}/service-due", headers=H)).json()["items"] == []

    due = await _services_every_180_days(db, org_id, devices=1, last_days_ago=175)
    o2 = await _open_job(db, org_id, device_id=due[0].id, lines=[("Oil change", 60, 20)])
    await db.commit()
    assert (await client.get(f"/api/sense/orders/{o2.id}/service-due", headers=H)).json()["items"] == []


@pytest.mark.asyncio
async def test_the_sweep_sends_briefs_for_shops_that_asked(client, db, outbox, monkeypatch):
    from backend.core import scheduled
    from backend.db import database
    H, org_id = await _register(client)
    await client.put("/api/sense/settings", json={"brief": {"hour": 0, "email": True}}, headers=H)
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Same(db))
    await scheduled.sweep_morning_briefs()
    assert len(outbox["email"]) == 1


class _Same:
    """Hands the sweep the test's own session (same database) as an async context manager."""
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_a_recipient_who_left_drops_off_without_locking_the_owner_out(client, db, outbox):
    from backend.models import Employee
    H, org_id = await _register(client)
    T = await _staff(client, H, {"can_create_tickets": True})
    U = await _staff(client, H, {"can_create_tickets": True})
    tid, uid = await _emp_id(client, T), await _emp_id(client, U)
    await client.put("/api/sense/settings", json={"brief": {"recipients": [tid, uid], "hour": 0, "email": True}}, headers=H)
    (await db.execute(select(Employee).where(Employee.id == tid))).scalar_one().active = False
    await db.commit()
    # the owner's next save still carries the old id: it is dropped, not refused
    r = await client.put("/api/sense/settings", json={"brief": {"recipients": [tid, uid]}}, headers=H)
    assert r.status_code == 200 and r.json()["brief"]["recipients"] == [uid]
    await sense.send_briefs(db, org_id, now=datetime.utcnow() + timedelta(days=1))
    emails = {e["to"] for e in outbox["email"]}
    gone = (await db.execute(select(Employee.email).where(Employee.id == tid))).scalar_one()
    assert gone not in emails and len(emails) == 2          # the owner and the one still here


@pytest.mark.asyncio
async def test_a_reader_without_finance_gets_no_amounts_by_email(client, db, outbox):
    from backend.models import Employee, Invoice
    H, org_id = await _register(client)
    T = await _staff(client, H, {"can_view_finance": False, "can_create_tickets": True})
    tid = await _emp_id(client, T)
    db.add(Invoice(org_id=org_id, invoice_number="I-7", status="issued", total=777, amount_paid=0,
                   due_at=datetime.utcnow() - timedelta(days=2)))
    await db.commit()
    await client.put("/api/sense/settings", json={"brief": {"recipients": [tid], "hour": 0, "email": True}}, headers=H)
    await sense.send_briefs(db, org_id, now=datetime.utcnow() + timedelta(days=1))
    staff_mail = (await db.execute(select(Employee.email).where(Employee.id == tid))).scalar_one()
    body = next(e["body"] for e in outbox["email"] if e["to"] == staff_mail)
    assert "overdue invoice" in body and "777" not in body


@pytest.mark.asyncio
async def test_one_failed_send_does_not_stop_the_others(client, db, monkeypatch):
    from backend.models import Employee
    H, org_id = await _register(client)
    T = await _staff(client, H, {"can_create_tickets": True})
    tid = await _emp_id(client, T)
    await client.put("/api/sense/settings", json={"brief": {"recipients": [tid], "hour": 0, "email": True}}, headers=H)
    got = []

    calls = []

    async def flaky(db_, org, *, recipient, subject, body):
        calls.append(recipient)
        if len(calls) == 1:                    # whoever is first hits a real failed statement
            await db_.execute(text("SELECT * FROM no_such_table"))
        got.append(recipient)
    monkeypatch.setattr(sense, "_send_email", flaky)
    assert await sense.send_briefs(db, org_id, now=datetime.utcnow() + timedelta(days=1)) == 1
    assert len(got) == 1


def test_brief_email_shows_amounts_in_the_shops_currency():
    text = sense.brief_text({"date": "2026-10-20", "shop": "Fix-It", "currency": "$",
                             "yesterday": {"jobs_closed": 2, "paid": 1250.5}, "today": {"due": 1},
                             "attention": [{"key": "overdue_invoices", "title": "1 overdue invoice", "count": 1, "amount": 80}]})
    assert "$1,250.50 taken" in text and "1 overdue invoice ($80.00)" in text and "2 jobs closed" in text
