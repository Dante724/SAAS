"""Tekly Sense sprint 4: collections, quote follow-up and win-back agents.

Off by default; when on, every message is a draft for the owner's OK unless the owner chose
otherwise. Consent is checked when drafting and again at send time, messages wait for the
shop's opening hours, caps stop pestering, a kill switch stops anything already approved,
and every agent email carries a one-click unsubscribe.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.services import notification_rules as nr
from backend.services import sense_agents as agents
from backend.tests.test_sense_sprint1 import _register, _staff

NOON = datetime(2026, 10, 21, 12, 0)          # inside default opening hours (09:00–18:00)


async def _shop(client, db, address="12 Harbour Rd, Springfield"):
    from backend.models import Organisation
    H, org_id = await _register(client)
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.timezone, org.company_address, org.company_phone = "UTC", address, "555 0100"
    await db.commit()
    return H, org_id


async def _customer(client, H, **kw):
    body = {"name": "Maria Lopez", "phone": "5551230000", "email": f"m{uuid.uuid4().hex[:5]}@x.dev", **kw}
    return (await client.post("/api/clients", json=body, headers=H)).json()["id"]


async def _overdue_invoice(db, org_id, client_id, days=5, total=250):
    from backend.models import Invoice
    inv = Invoice(org_id=org_id, invoice_number=f"INV-{uuid.uuid4().hex[:4]}", client_id=client_id,
                  status="issued", total=total, amount_paid=0, due_at=NOON - timedelta(days=days))
    db.add(inv)
    await db.commit()
    return inv


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_send(db, org_id, channel, *, recipient, subject, body, headers=None):
        out.append({"channel": channel, "to": recipient, "body": body, "headers": headers})
        return {"status": "sent"}
    monkeypatch.setattr(nr, "_send", fake_send)
    return out


async def _turn_on(client, H, agent="collections", **cfg):
    r = await client.put("/api/sense/settings", json={"agents": {agent: {"on": True, **cfg}}}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


async def _logs(db, org_id):
    from backend.models import NotificationLog
    return (await db.execute(select(NotificationLog).where(
        NotificationLog.org_id == org_id, NotificationLog.trigger_event.like("agent.%")))).scalars().all()


# ── Off, draft, approve ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_agents_are_off_until_the_owner_turns_them_on(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    s = (await client.get("/api/sense/settings", headers=H)).json()["agents"]
    assert s["collections"]["on"] is False and s["collections"]["auto_send"] is False
    assert await agents.run_agents(db, org_id, now=NOON) == 0 and await _logs(db, org_id) == []


@pytest.mark.asyncio
async def test_a_drafted_reminder_waits_for_the_owner_then_sends(client, db, sent):
    from backend.models import AuditLog
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    inv = await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=5))
    assert sent == []                                            # a draft is never sent on its own

    for m in await _logs(db, org_id):          # approval runs on the real clock: keep the draft fresh
        m.created_at = datetime.utcnow()
    await db.commit()
    drafts = (await client.get("/api/sense/agents/drafts", headers=H)).json()["items"]
    assert len(drafts) == 1 and inv.invoice_number in drafts[0]["body"] and drafts[0]["agent"] == "collections"
    r = await client.post("/api/sense/agents/drafts/approve", json={"ids": [drafts[0]["id"]]}, headers=H)
    assert r.status_code == 200 and r.json()["approved"] == 1
    tomorrow_noon = (datetime.utcnow() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    await nr.run_due(db, org_id=org_id, now=tomorrow_noon)
    assert len(sent) == 1 and sent[0]["channel"] == "sms" and inv.invoice_number in sent[0]["body"]
    audit = (await db.execute(select(AuditLog).where(AuditLog.org_id == org_id,
                                                     AuditLog.action == "sense.agent.send_batch"))).scalars().first()
    assert audit and "body" not in str(audit.diff) and "555" not in str(audit.diff)


@pytest.mark.asyncio
async def test_discarded_drafts_never_go(client, db, sent):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    await agents.run_agents(db, org_id, now=NOON)
    d = (await client.get("/api/sense/agents/drafts", headers=H)).json()["items"][0]
    await client.post("/api/sense/agents/drafts/discard", json={"ids": [d["id"]]}, headers=H)
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(days=1))
    assert sent == [] and (await client.get("/api/sense/agents/drafts", headers=H)).json()["items"] == []


@pytest.mark.asyncio
async def test_auto_send_skips_the_draft_step(client, db, sent):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=NOON)
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert len(sent) == 1


# ── Quotes and win-back ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_quote_follow_up_nudges_an_unanswered_quote(client, db):
    from backend.models import Location, Order
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    q = Order(org_id=org_id, order_number="Q-1", location_id=loc, client_id=cid, is_estimate=True, status="new",
              complaint="x", created_at=NOON - timedelta(days=3))
    approved = Order(org_id=org_id, order_number="Q-2", location_id=loc, client_id=cid, is_estimate=True, status="new",
                     complaint="x", created_at=NOON - timedelta(days=3), estimate_approved_at=NOON - timedelta(days=1))
    db.add_all([q, approved])
    await db.commit()
    await _turn_on(client, H, agent="quote_followup")
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    assert "Q-1" in (await _logs(db, org_id))[0].body


@pytest.mark.asyncio
async def test_win_back_needs_marketing_consent(client, db):
    from backend.models import Location, Order
    H, org_id = await _shop(client, db)
    yes = await _customer(client, H, name="Yes Please")
    no = await _customer(client, H, name="No Thanks")
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    for cid in (yes, no):
        db.add(Order(org_id=org_id, order_number=f"OLD-{cid[:4]}", location_id=loc, client_id=cid, status="closed",
                     complaint="x", created_at=NOON - timedelta(days=300), closed_at=NOON - timedelta(days=300)))
    await db.commit()
    await client.post(f"/api/compliance/gdpr/client/{yes}/consent", json={"channel": "marketing", "granted": True}, headers=H)
    await _turn_on(client, H, agent="win_back", channel="email")
    assert await agents.run_agents(db, org_id, now=NOON) == 1
    log = (await _logs(db, org_id))[0]
    assert log.client_id == yes and "/unsubscribe/" in log.body
    assert "12 Harbour Rd, Springfield" in log.body                # who it's from, with a postal address


# ── Caps ────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_caps_stop_pestering(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _overdue_invoice(db, org_id, cid)                       # same customer, second invoice
    await _turn_on(client, H, auto_send=True)                     # scheduled, so none go stale as drafts
    assert await agents.run_agents(db, org_id, now=NOON) == 1     # one per customer per 72 hours
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=1)) == 0
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=4)) == 1   # the other invoice
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=8)) == 1   # 7 days on, first again
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(days=12)) == 0  # 3 in 30 days for this customer


@pytest.mark.asyncio
async def test_the_daily_cap_holds(client, db):
    H, org_id = await _shop(client, db)
    for i in range(4):
        await _overdue_invoice(db, org_id, await _customer(client, H, name=f"C {i}", phone=f"55500000{i}"))
    await client.put("/api/sense/settings", json={"agents": {"collections": {"on": True}, "daily_cap": 2}}, headers=H)
    assert await agents.run_agents(db, org_id, now=NOON) == 2
    assert await agents.run_agents(db, org_id, now=NOON + timedelta(hours=2)) == 0


# ── Consent, quiet hours, kill switch at send time ──────────────────────────────
@pytest.mark.asyncio
async def test_no_consent_no_draft(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H, consent_phone=False)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    assert await agents.run_agents(db, org_id, now=NOON) == 0


@pytest.mark.asyncio
async def test_after_hours_an_approved_message_waits_for_opening(client, db, sent):
    from backend.models import NotificationLog
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=NOON)
    three_am = datetime(2026, 10, 22, 3, 0)
    log = (await _logs(db, org_id))[0]
    log_id = log.id
    log.send_at = three_am - timedelta(minutes=1)
    await db.commit()
    await nr.run_due(db, org_id=org_id, now=three_am)
    assert sent == []
    db.expire_all()
    log = (await db.execute(select(NotificationLog).where(NotificationLog.id == log_id))).scalar_one()
    assert log.status == "scheduled" and log.send_at == datetime(2026, 10, 22, 9, 0)


@pytest.mark.asyncio
async def test_turning_the_agent_off_stops_messages_already_approved(client, db, sent):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=NOON)
    await client.put("/api/sense/settings", json={"agents": {"collections": {"on": False}}}, headers=H)
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert sent == []
    assert (await _logs(db, org_id))[0].status == "skipped"


@pytest.mark.asyncio
async def test_the_platform_switch_stops_every_agent(client, db, sent, monkeypatch):
    from backend.config import settings
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=NOON)
    monkeypatch.setattr(settings, "SENSE_AGENTS_DISABLED", True, raising=False)
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert sent == [] and await agents.run_agents(db, org_id, now=NOON + timedelta(days=8)) == 0


# ── Unsubscribe ─────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_one_click_unsubscribe(client, db):
    from backend.models import Client, ConsentLog
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    token = agents.unsubscribe_token(org_id, cid)
    page = await client.get(f"/api/public/unsubscribe/{token}")
    assert page.status_code == 200 and "<form" in page.text
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    assert c.consent_email is True                                   # looking doesn't change anything
    r = await client.post(f"/api/public/unsubscribe/{token}")
    assert r.status_code == 200
    db.expire_all()
    c = (await db.execute(select(Client).where(Client.id == cid))).scalar_one()
    assert c.consent_email is False
    chans = {(x.channel, x.granted) for x in (await db.execute(select(ConsentLog).where(
        ConsentLog.client_id == cid))).scalars().all()}
    assert ("email", False) in chans and ("marketing", False) in chans
    bad = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert (await client.post(f"/api/public/unsubscribe/{bad}")).status_code == 404


# ── Who approves, and only their own shop ───────────────────────────────────────
@pytest.mark.asyncio
async def test_only_approvers_see_and_send_drafts_and_only_their_shop(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H)
    await agents.run_agents(db, org_id, now=NOON)
    draft_id = (await client.get("/api/sense/agents/drafts", headers=H)).json()["items"][0]["id"]

    T = await _staff(client, H, {"can_create_tickets": True, "can_manage_settings": True})
    assert (await client.get("/api/sense/agents/drafts", headers=T)).status_code == 403
    A = await _staff(client, H, {"can_approve_agent_sends": True})
    assert len((await client.get("/api/sense/agents/drafts", headers=A)).json()["items"]) == 1

    HB, _ = await _shop(client, db)
    assert (await client.get("/api/sense/agents/drafts", headers=HB)).json()["items"] == []
    r = await client.post("/api/sense/agents/drafts/approve", json={"ids": [draft_id]}, headers=HB)
    assert r.json()["approved"] == 0



# ── Review fixes: stale messages, marketing rules, hours, headers ────────────────
@pytest.mark.asyncio
async def test_a_reminder_for_an_invoice_paid_since_is_not_sent(client, db, sent):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    inv = await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True)
    await agents.run_agents(db, org_id, now=NOON)
    inv.status, inv.amount_paid = "paid", inv.total
    await db.commit()
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    assert sent == [] and (await _logs(db, org_id))[0].status == "skipped"


@pytest.mark.asyncio
async def test_drafts_go_stale_after_a_week(client, db):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid, days=30)
    await _turn_on(client, H)
    await agents.run_agents(db, org_id, now=datetime.utcnow() - timedelta(days=9))
    d = (await client.get("/api/sense/agents/drafts", headers=H)).json()["items"][0]
    r = await client.post("/api/sense/agents/drafts/approve", json={"ids": [d["id"]]}, headers=H)
    assert r.json()["approved"] == 0 and (await _logs(db, org_id))[0].status == "expired"


@pytest.mark.asyncio
async def test_win_back_needs_a_postal_address_and_email(client, db):
    from backend.models import Location, Order
    H, org_id = await _shop(client, db, address="")
    cid = await _customer(client, H)
    loc = (await db.execute(select(Location.id).where(Location.org_id == org_id))).scalars().first()
    db.add(Order(org_id=org_id, order_number="OLD-1", location_id=loc, client_id=cid, status="closed", complaint="x",
                 created_at=NOON - timedelta(days=300), closed_at=NOON - timedelta(days=300)))
    await db.commit()
    await client.post(f"/api/compliance/gdpr/client/{cid}/consent", json={"channel": "marketing", "granted": True}, headers=H)
    await _turn_on(client, H, agent="win_back", channel="email")
    assert await agents.run_agents(db, org_id, now=NOON) == 0       # no address on file
    r = await client.put("/api/sense/settings", json={"agents": {"win_back": {"channel": "sms"}}}, headers=H)
    assert r.status_code == 422
    r = await client.put("/api/sense/settings", json={"agents": {"collections": {"channel": "whatsapp"}}}, headers=H)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_agent_emails_carry_one_click_unsubscribe_headers(client, db, sent):
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    await _overdue_invoice(db, org_id, cid)
    await _turn_on(client, H, auto_send=True, channel="email")
    await agents.run_agents(db, org_id, now=NOON)
    await nr.run_due(db, org_id=org_id, now=NOON + timedelta(minutes=1))
    h = sent[0]["headers"]
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click" and "/api/public/unsubscribe/" in h["List-Unsubscribe"]


@pytest.mark.asyncio
async def test_opening_hours_follow_the_location_and_skip_closed_days(client, db):
    from backend.models import Location, Organisation
    H, org_id = await _shop(client, db)
    org = (await db.execute(select(Organisation).where(Organisation.id == org_id))).scalar_one()
    org.timezone = "America/New_York"
    loc = (await db.execute(select(Location).where(Location.org_id == org_id))).scalars().first()
    loc.working_hours = {"wed": {"open": "10:00", "close": "16:00"}, "thu": {"closed": True},
                         "fri": {"open": "08:00", "close": "17:00"}}
    await db.commit()
    wed_8am_ny = datetime(2026, 10, 21, 12, 0)                     # 08:00 New York (UTC-4)
    assert await agents.next_open(db, org, wed_8am_ny, loc.id) == datetime(2026, 10, 21, 14, 0)   # 10:00 NY
    wed_5pm_ny = datetime(2026, 10, 21, 21, 0)                     # after close; Thursday closed
    assert await agents.next_open(db, org, wed_5pm_ny, loc.id) == datetime(2026, 10, 23, 12, 0)   # Fri 08:00 NY
    sat_noon_ny = datetime(2026, 10, 24, 16, 0)
    sun = await agents.next_open(db, org, sat_noon_ny + timedelta(days=1), loc.id, marketing=True)
    assert sun.weekday() == 0                                      # marketing never on a Sunday


@pytest.mark.asyncio
async def test_unsubscribing_twice_records_it_once(client, db):
    from backend.models import ConsentLog
    H, org_id = await _shop(client, db)
    cid = await _customer(client, H)
    token = agents.unsubscribe_token(org_id, cid)
    await client.post(f"/api/public/unsubscribe/{token}")
    r = await client.post(f"/api/public/unsubscribe/{token}")
    assert r.status_code == 200 and r.headers.get("referrer-policy") == "no-referrer"
    rows = (await db.execute(select(ConsentLog).where(ConsentLog.client_id == cid))).scalars().all()
    assert len(rows) == 2
