"""Tekly Sense sprint 4 edge cases (from the tester's review): time zones and DST, location hours
and closed days, partly paid invoices, who gets skipped, caps across agents, unsubscribe and STOP."""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services import notification_rules as nr
from backend.services import sense_agents as agents
from backend.tests.test_sense_sprint4 import (
    NOON,
    _customer,
    _logs,
    _overdue_invoice,
    _shop,
    _turn_on,  # noqa: F401 — fixture
    sent,
)

UTC_SHOP = None


async def _org(db, org_id, tz=None):
    from backend.models import Organisation
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    if tz:
        org.timezone = tz
        await db.commit()
    return org


async def _loc(db, org_id):
    from backend.models import Location
    return (await db.execute(select(Location).where(Location.org_id == org_id))).scalars().first()


async def _addr(db, org_id):
    org = await _org(db, org_id)
    org.company_address = "1 Main St, Springfield"
    await db.commit()


async def _client_row(db, cid):
    from backend.models import Client
    return (await db.execute(select(Client).where(Client.id == cid))).scalar_one()


# ── Opening hours in the shop's own time zone ───────────────────────────────────
@pytest.mark.asyncio
async def test_next_open_new_york(client, db):
    _, org_id = await _shop(client, db)
    org = await _org(db, org_id, "America/New_York")
    # 12:00 UTC = 08:00 EDT -> opens 09:00 EDT = 13:00 UTC
    assert await agents.next_open(db, org, NOON) == datetime(2026, 10, 21, 13, 0)
    # 15:00 UTC = 11:00 EDT -> open now
    assert await agents.next_open(db, org, datetime(2026, 10, 21, 15, 0)) == datetime(2026, 10, 21, 15, 0)
    # 23:00 UTC = 19:00 EDT -> next day 09:00 EDT
    assert await agents.next_open(db, org, datetime(2026, 10, 21, 23, 0)) == datetime(2026, 10, 22, 13, 0)


@pytest.mark.asyncio
async def test_next_open_new_york_across_dst_end(client, db):
    _, org_id = await _shop(client, db)
    org = await _org(db, org_id, "America/New_York")
    # Sat 31 Oct 2026 23:30 UTC = 19:30 EDT; DST ends Sun 1 Nov -> 09:00 EST = 14:00 UTC
    assert await agents.next_open(db, org, datetime(2026, 10, 31, 23, 30)) == datetime(2026, 11, 1, 14, 0)
    # spring forward: Sat 13 Mar 2027 23:30 UTC = 18:30 EST; Sun 14 Mar 09:00 EDT = 13:00 UTC
    assert await agents.next_open(db, org, datetime(2027, 3, 13, 23, 30)) == datetime(2027, 3, 14, 13, 0)


@pytest.mark.asyncio
async def test_next_open_kolkata(client, db):
    _, org_id = await _shop(client, db)
    org = await _org(db, org_id, "Asia/Kolkata")
    # 03:00 UTC = 08:30 IST -> 09:00 IST = 03:30 UTC
    assert await agents.next_open(db, org, datetime(2026, 10, 21, 3, 0)) == datetime(2026, 10, 21, 3, 30)
    # 13:00 UTC = 18:30 IST -> next day 09:00 IST
    assert await agents.next_open(db, org, datetime(2026, 10, 21, 13, 0)) == datetime(2026, 10, 22, 3, 30)
    # 20:00 UTC = 01:30 IST next day -> 09:00 IST that same local day
    assert await agents.next_open(db, org, datetime(2026, 10, 21, 20, 0)) == datetime(2026, 10, 22, 3, 30)


@pytest.mark.asyncio
async def test_auto_send_in_new_york_waits_for_local_opening(client, db, sent):
    H, org_id = await _shop(client, db)
    await _org(db, org_id, "America/New_York")
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    assert (await _logs(db, org_id))[0].send_at == datetime(2026, 10, 21, 13, 0)
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert sent == []


@pytest.mark.asyncio
async def test_next_open_location_custom_hours_direct(client, db):
    _, org_id = await _shop(client, db)
    org = await _org(db, org_id, "UTC")
    loc = await _loc(db, org_id)
    loc.working_hours = {"wed": {"open": "10:00", "close": "14:00"}}
    await db.commit()
    # Wed 21 Oct 2026, 15:00 is after a 14:00 close -> Thursday falls back to default 09:00
    assert await agents.next_open(db, org, datetime(2026, 10, 21, 9, 30), loc.id) == datetime(2026, 10, 21, 10, 0)
    assert await agents.next_open(db, org, datetime(2026, 10, 21, 15, 0), loc.id) == datetime(2026, 10, 22, 9, 0)


@pytest.mark.asyncio
async def test_agent_messages_respect_the_shops_location_hours(client, db):
    """Single-location shop open 10:00–14:00 on Wednesday: an auto message drafted at 09:30
    should wait for 10:00. (run_agents/approve/send_check never pass a location.)"""
    H, org_id = await _shop(client, db)
    loc = await _loc(db, org_id)
    loc.working_hours = {"wed": {"open": "10:00", "close": "14:00"}}
    await db.commit()
    from backend.models import Order
    cid = await _customer(client, H)
    o = Order(org_id=org_id, order_number="L-1", location_id=loc.id, client_id=cid, status="closed",
              complaint="x", created_at=NOON - timedelta(days=10))
    db.add(o)
    await db.flush()
    inv = await _overdue_invoice(db, org_id, cid)
    inv.order_id = o.id
    await db.commit()
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=datetime(2026, 10, 21, 9, 30))
    assert (await _logs(db, org_id))[0].send_at == datetime(2026, 10, 21, 10, 0)


@pytest.mark.asyncio
async def test_invoice_without_order_in_single_location_shop_uses_its_hours(client, db):
    """Most invoices may have no order; a one-location shop's hours should still apply."""
    H, org_id = await _shop(client, db)
    loc = await _loc(db, org_id)
    loc.working_hours = {"wed": {"open": "10:00", "close": "14:00"}}
    await db.commit()
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=datetime(2026, 10, 21, 9, 30))
    assert (await _logs(db, org_id))[0].send_at == datetime(2026, 10, 21, 10, 0)


@pytest.mark.asyncio
async def test_next_open_skips_a_closed_day(client, db):
    _, org_id = await _shop(client, db)
    org = await _org(db, org_id, "UTC")
    loc = await _loc(db, org_id)
    loc.working_hours = {"sun": {"closed": True}}
    await db.commit()
    # Sat 24 Oct 2026 20:00 -> Sunday closed -> Monday 09:00
    assert await agents.next_open(db, org, datetime(2026, 10, 24, 20, 0), loc.id) == datetime(2026, 10, 26, 9, 0)


# ── What gets drafted ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_partially_paid_invoice_quotes_the_balance(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    inv = await _overdue_invoice(db, org_id, cid, total=250)
    inv.amount_paid, inv.status = 100, "partial"
    await db.commit()
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    body = (await _logs(db, org_id))[0].body
    assert "150.00" in body and "250.00" not in body


@pytest.mark.asyncio
async def test_fully_paid_but_status_open_is_skipped(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    inv = await _overdue_invoice(db, org_id, cid, total=250)
    inv.amount_paid = 250
    await db.commit()
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 0


@pytest.mark.asyncio
async def test_invoice_without_client_is_skipped(client, db):
    H, org_id = await _shop(client, db)
    await _overdue_invoice(db, org_id, None)
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 0


@pytest.mark.asyncio
async def test_quote_converted_to_order_is_excluded(client, db):
    from backend.models import Order
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    loc = await _loc(db, org_id)
    real = Order(org_id=org_id, order_number="O-9", location_id=loc.id, client_id=cid, status="new",
                 complaint="x", created_at=NOON - timedelta(days=1))
    db.add(real)
    await db.flush()
    q = Order(org_id=org_id, order_number="Q-9", location_id=loc.id, client_id=cid, is_estimate=True, status="new",
              complaint="x", created_at=NOON - timedelta(days=3), converted_order_id=real.id)
    db.add(q)
    await db.commit()
    await _turn_on(client, H, agent="quote_followup")
    assert await agents.run_agents(db, org_id, now=NOON) == 0


@pytest.mark.asyncio
async def test_win_back_skips_customer_with_a_recent_order(client, db):
    from backend.models import Order
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    loc = await _loc(db, org_id)
    for days in (400, 300, 10):
        db.add(Order(org_id=org_id, order_number=f"W-{days}", location_id=loc.id, client_id=cid, status="closed",
                     complaint="x", created_at=NOON - timedelta(days=days)))
    await db.commit()
    await _addr(db, org_id)
    await client.post(f"/api/compliance/gdpr/client/{cid}/consent", json={"channel": "marketing", "granted": True}, headers=H)
    await _turn_on(client, H, agent="win_back", channel="email")
    assert await agents.run_agents(db, org_id, now=NOON) == 0


@pytest.mark.asyncio
async def test_whatsapp_is_refused_and_never_drafted(client, db):
    """Sprint changed scope: WhatsApp is not offered yet (templates). The API refuses it, and a
    stored 'whatsapp' setting (e.g. from an older build) drafts nothing."""
    from backend.services import sense
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H, phone="5559876543")
    await _overdue_invoice(db, org_id, cid)
    r = await client.put("/api/sense/settings", json={"agents": {"collections": {"on": True, "channel": "whatsapp"}}}, headers=H)
    assert r.status_code == 422
    await _turn_on(client, H)
    await sense.save_settings(db, org_id, enabled=None, features=None, agents={"collections": {"channel": "whatsapp"}})
    await db.commit()
    assert await agents.run_agents(db, org_id, now=NOON) == 0


@pytest.mark.asyncio
async def test_sms_channel_uses_the_phone_with_opt_out(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H, phone="5559876543")
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    log = (await _logs(db, org_id))[0]
    assert log.channel == "sms" and "5559876543" in log.recipient and "@" not in log.recipient
    assert "/unsubscribe/" not in log.body and "STOP" in log.body.upper()


@pytest.mark.asyncio
async def test_email_channel_without_an_email_is_skipped(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    c = await _client_row(db, cid)
    c.email = None
    await db.commit()
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, channel="email")
    assert await agents.run_agents(db, org_id, now=NOON) == 0


# ── Caps ────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_daily_cap_counts_across_agents(client, db):
    from backend.models import Order
    H, org_id = await _shop(client, db)
    a = await _customer(client, H, name="A A", phone="5550000101")
    await _overdue_invoice(db, org_id, a)
    loc = await _loc(db, org_id)
    for i in range(2):
        cid = await _customer(client, H, name=f"Q {i}", phone=f"55500002{i}0")
        db.add(Order(org_id=org_id, order_number=f"QQ-{i}", location_id=loc.id, client_id=cid, is_estimate=True,
                     status="new", complaint="x", created_at=NOON - timedelta(days=3)))
    await db.commit()
    await client.put("/api/sense/settings", json={"agents": {"collections": {"on": True}, "daily_cap": 2}}, headers=H)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    await _turn_on(client, H, agent="quote_followup")
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(hours=1)) == 1   # 1 collections + 1 quote = 2
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(hours=2)) == 0


@pytest.mark.asyncio
async def test_discarded_draft_not_redrafted_within_7_days(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    ids = [m.id for m in await _logs(db, org_id)]
    assert await agents.discard(db, org_id, ids) == 1
    await db.commit()
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(hours=1)) == 0
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=4)) == 0
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=8)) == 1


# ── Approve, unsubscribe, STOP ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_approve_twice_is_idempotent(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    await agents.run_agents(db, org_id, now=NOON)
    ids = [m.id for m in await _logs(db, org_id)]
    assert await agents.approve(db, org_id, ids, now=NOON) == 1
    await db.commit()
    first = (await _logs(db, org_id))[0].send_at
    assert first == NOON
    assert await agents.approve(db, org_id, ids, now=NOON + timedelta(hours=10)) == 0
    await db.commit()
    log = (await _logs(db, org_id))[0]
    assert log.status == "scheduled" and log.send_at == first
    r = await client.post("/api/sense/agents/drafts/approve", json={"ids": ids}, headers=H)
    assert r.status_code == 200 and r.json()["approved"] == 0


@pytest.mark.asyncio
async def test_approve_after_hours_waits_for_opening(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    await agents.run_agents(db, org_id, now=NOON)
    ids = [m.id for m in await _logs(db, org_id)]
    await agents.approve(db, org_id, ids, now=datetime(2026, 10, 21, 22, 0))
    await db.commit()
    assert (await _logs(db, org_id))[0].send_at == datetime(2026, 10, 22, 9, 0)


@pytest.mark.asyncio
async def test_unsubscribe_token_with_another_orgs_client_fails(client, db):
    HA, org_a = await _shop(client, db)
    HB, org_b = await _shop(client, db)
    cid_b = await _customer(client, HB)
    token = agents.unsubscribe_token(org_a, cid_b)
    assert (await client.post(f"/api/public/unsubscribe/{token}")).status_code == 404
    db.expire_all()
    assert (await _client_row(db, cid_b)).consent_email is True


@pytest.mark.asyncio
async def test_unsubscribe_garbage_tokens(client, db):
    for t in ("x", "abc.def", "." * 5, "a" * 400):
        assert (await client.get(f"/api/public/unsubscribe/{t}")).status_code == 404


@pytest.mark.asyncio
async def test_stop_after_approval_skips_the_sms(client, db, sent):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=NOON)
    c = await _client_row(db, cid)
    c.consent_phone = False
    await db.commit()
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert sent == []
    db.expire_all()
    assert (await _logs(db, org_id))[0].status == "skipped"


@pytest.mark.asyncio
async def test_win_back_marketing_withdrawn_after_approval_is_skipped(client, db, sent):
    from backend.models import Order
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    loc = await _loc(db, org_id)
    db.add(Order(org_id=org_id, order_number="WB", location_id=loc.id, client_id=cid, status="closed",
                 complaint="x", created_at=NOON - timedelta(days=300)))
    await db.commit()
    await client.post(f"/api/compliance/gdpr/client/{cid}/consent", json={"channel": "marketing", "granted": True}, headers=H)
    await _addr(db, org_id)
    await _turn_on(client, H, agent="win_back", channel="email", auto_send=True)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    await client.post(f"/api/compliance/gdpr/client/{cid}/consent", json={"channel": "marketing", "granted": False}, headers=H)
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert sent == []


@pytest.mark.asyncio
async def test_win_back_without_recent_order_is_drafted_with_footer(client, db):
    """Positive control for the recent-order test above."""
    from backend.models import Order
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    loc = await _loc(db, org_id)
    db.add(Order(org_id=org_id, order_number="W-1", location_id=loc.id, client_id=cid, status="closed",
                 complaint="x", created_at=NOON - timedelta(days=300)))
    await db.commit()
    await _addr(db, org_id)
    await client.post(f"/api/compliance/gdpr/client/{cid}/consent", json={"channel": "marketing", "granted": True}, headers=H)
    await _turn_on(client, H, agent="win_back", channel="email")
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    body = (await _logs(db, org_id))[0].body
    assert "1 Main St" in body and "/unsubscribe/" in body
    # 180-day gap: not again after 30 days
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=30)) == 0


@pytest.mark.asyncio
async def test_entity_gap_single_invoice(client, db):
    """One invoice, one customer: after 72h the customer gap is gone but the 7-day invoice gap holds."""
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=4)) == 0
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=8)) == 1
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=40)) == 0   # max 2 per invoice


@pytest.mark.asyncio
async def test_unsubscribe_get_changes_nothing_and_sig_is_checked(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    import base64
    payload = base64.urlsafe_b64encode(f"{org_id}|{cid}".encode()).decode().rstrip("=")
    forged = f"{payload}.{'0' * 32}"
    assert (await client.post(f"/api/public/unsubscribe/{forged}")).status_code == 404
    db.expire_all()
    assert (await _client_row(db, cid)).consent_email is True


@pytest.mark.asyncio
async def test_turning_sense_off_entirely_stops_approved_messages(client, db, sent):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    assert (await client.put("/api/sense/settings", json={"enabled": False}, headers=H)).status_code == 200
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert sent == []


@pytest.mark.asyncio
async def test_quote_older_than_30_days_is_not_chased(client, db):
    from backend.models import Order
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    loc = await _loc(db, org_id)
    db.add(Order(org_id=org_id, order_number="Q-OLD", location_id=loc.id, client_id=cid, is_estimate=True,
                 status="new", complaint="x", created_at=NOON - timedelta(days=31)))
    await db.commit()
    await _turn_on(client, H, agent="quote_followup")
    assert await agents.run_agents(db, org_id, now=NOON) == 0
