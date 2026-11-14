"""Wave 4 group B: the suspension gaps PR #208 left open (FOLLOWUPS: CLI-01 follow-ups).

1. A pay link created before the shop was suspended refuses payment, with a customer-safe message.
2. Inbound provider webhooks (chats SMS/WhatsApp/Meta/generic, call events from Twilio, Aircall and
   the generic call hook) still answer 2xx so the provider stops retrying, but store nothing, raise
   no inquiry and alert nobody for a suspended shop. The public feedback form refuses politely.
3. A queued message that missed its send time by more than a day (for example, it waited out a long
   suspension) is skipped, not sent late.
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.config import settings
from backend.models import Call, ChatMessage, Client, Lead, Organisation
from backend.services.org_live import PUBLIC_OFF_MESSAGE
from backend.tests.lapse_modes import OFF_MODES, off_mode, set_off_status
from backend.tests.test_opd_telephony import (  # noqa: F401 — fake_twilio is a fixture
    connect,
    fake_twilio,
    inbound,
    org_of,
    post_signed,
)
from backend.tests.test_public_pay_page import _payment_link, _register

pytestmark = pytest.mark.asyncio


# Owner decision 7: a shop past its lapse grace (sub_end + SUB_LAPSE_GRACE_DAYS) is held exactly like
# a suspended one, so every case here runs both ways (backend/tests/lapse_modes.py).
@pytest.fixture(autouse=True, params=OFF_MODES)
def _off_mode(request):
    with off_mode(request.param):
        yield request.param


async def _set_status(db, org_id, status):
    await set_off_status(db, org_id, status)


async def _count(db, model, *where):
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar()


# ── 1. Pay links ──────────────────────────────────────────────────────────────────────────────
async def test_pay_link_of_a_suspended_shop_is_not_payable(client, db):
    from backend.models import PaymentIntent
    H = await _register(client, "Paused Phone Fix")
    org_id = await org_of(client, H)
    intent_id = await _payment_link(client, H)
    assert (await client.get(f"/api/public/pay/{intent_id}")).json()["payable"] is True

    await _set_status(db, org_id, "suspended")
    info = (await client.get(f"/api/public/pay/{intent_id}")).json()
    assert info["payable"] is False and info["checkout_url"] is None
    assert info["message"] == "This shop isn't taking payments online right now. Please contact the shop directly."
    assert "suspend" not in str(info).lower()          # nothing internal reaches the customer
    assert info["shop_name"] == "Paused Phone Fix"     # still says who to call
    r = await client.post(f"/api/payments/intents/{intent_id}/simulate-pay")
    assert r.status_code == 403 and "suspend" not in r.text.lower()
    i = (await db.execute(select(PaymentIntent).where(PaymentIntent.id == intent_id)
                          .execution_options(populate_existing=True))).scalar_one()
    assert i.status != "succeeded" and i.payment_id is None

    await _set_status(db, org_id, "active")            # resumed: the same link works again
    info = (await client.get(f"/api/public/pay/{intent_id}")).json()
    assert info["payable"] is True and info.get("message") is None
    assert (await client.post(f"/api/payments/intents/{intent_id}/simulate-pay")).status_code == 200


# ── 2. Inbound provider webhooks ──────────────────────────────────────────────────────────────
async def _msgs(db, org_id):
    from backend.models import ChatThread
    return await _count(db, ChatMessage, ChatMessage.thread_id.in_(
        select(ChatThread.id).where(ChatThread.org_id == org_id)))


async def test_inbound_chat_webhooks_ack_but_store_nothing_for_a_suspended_shop(client, tenant, db):
    org = await org_of(client, tenant)
    sec = (await client.get("/api/settings/webhook-secret", headers=tenant)).json()["webhook_secret"]
    await _set_status(db, org, "suspended")

    r = await client.post(f"/api/chats/webhook/twilio?org_id={org}",
                          data={"From": "+15559700", "Body": "is it ready?", "MessageSid": "SM-s1"})
    assert r.status_code == 200 and "<Response>" in r.text
    r = await client.post(f"/api/chats/webhook/{org}", headers={"X-Webhook-Token": sec},
                          json={"channel": "sms", "handle": "+1555", "body": "hi", "external_message_id": "g1"})
    assert r.status_code == 201
    r = await client.post(f"/api/inbox/webhook/{org}", headers={"X-Webhook-Token": sec},
                          json={"channel": "sms", "handle": "+1555", "body": "hi", "external_message_id": "g2"})
    assert r.status_code == 201
    assert await _msgs(db, org) == 0

    await _set_status(db, org, "active")
    r = await client.post(f"/api/chats/webhook/twilio?org_id={org}",
                          data={"From": "+15559700", "Body": "is it ready?", "MessageSid": "SM-s2"})
    assert r.status_code == 200
    assert await _msgs(db, org) == 1


async def test_stop_still_turns_texts_off_while_suspended(client, tenant, db):
    """Opting out is compliance, like the unsubscribe link: it works even for a suspended shop."""
    org = await org_of(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "Sam", "phone": "+15559711"},
                             headers=tenant)).json()["id"]
    c = await db.get(Client, cid)
    c.consent_phone = True
    await db.commit()
    await _set_status(db, org, "suspended")
    r = await client.post(f"/api/chats/webhook/twilio?org_id={org}",
                          data={"From": "+15559711", "Body": "STOP", "MessageSid": "SM-stop"})
    assert r.status_code == 200
    await db.refresh(c)
    assert c.consent_phone is False
    assert await _msgs(db, org) == 0


async def test_generic_call_webhook_acks_but_records_nothing_for_a_suspended_shop(client, tenant, db):
    org = await org_of(client, tenant)
    o = await db.get(Organisation, org)
    o.webhook_secret = "whsec-test-0123456789"
    await db.commit()
    await _set_status(db, org, "suspended")
    r = await client.post(f"/api/comms/calls/webhook?org_id={org}", headers={"X-Webhook-Token": o.webhook_secret},
                          json={"provider": "pbx", "from_number": "+14155550100", "status": "no-answer"})
    assert r.status_code == 201, r.text
    assert await _count(db, Call, Call.org_id == org) == 0
    assert await _count(db, Lead, Lead.org_id == org) == 0
    # still authenticated first: a wrong token is refused as before
    r = await client.post(f"/api/comms/calls/webhook?org_id={org}", headers={"X-Webhook-Token": "nope"},
                          json={"provider": "pbx", "from_number": "+14155550100", "status": "no-answer"})
    assert r.status_code in (401, 403)


async def test_twilio_voice_rejects_calls_to_a_suspended_shop(client, tenant, fake_twilio, db):
    await connect(client, tenant)
    org = await org_of(client, tenant)
    await _set_status(db, org, "suspended")
    r = await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound())
    assert r.status_code == 200 and "<Reject" in r.text and "<Dial" not in r.text
    r = await post_signed(client, f"/api/comms/twilio/status?org_id={org}",
                          inbound(CallStatus="no-answer"))
    assert r.status_code == 200
    assert await _count(db, Call, Call.org_id == org) == 0
    assert await _count(db, Lead, Lead.org_id == org) == 0

    await _set_status(db, org, "active")
    r = await post_signed(client, f"/api/comms/twilio/voice?org_id={org}", inbound())
    assert r.status_code == 200 and "<Reject" not in r.text
    assert await _count(db, Call, Call.org_id == org) == 1


async def test_aircall_webhook_acks_but_records_nothing_for_a_suspended_shop(client, tenant, db, monkeypatch):
    from backend.tests.test_wsc_inquiries import _aircall_shop, aircall_event
    org = await _aircall_shop(client, tenant, monkeypatch)
    await _set_status(db, org, "suspended")
    r = await client.post(f"/api/telephony/aircall/webhook?org_id={org}",
                          json=aircall_event("call.ended", missed_reason="agents_did_not_answer"))
    assert r.status_code == 200, r.text
    assert await _count(db, Call, Call.org_id == org) == 0
    assert await _count(db, Lead, Lead.org_id == org) == 0
    assert settings.AIRCALL_ENABLED


async def test_public_feedback_form_refuses_politely_for_a_suspended_shop(client, tenant, db):
    from backend.models import Feedback
    org = await org_of(client, tenant)
    cid = (await client.post("/api/clients", json={"name": "F"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"complaint": "x", "client_id": cid}, headers=tenant)).json()["id"]
    fb = Feedback(org_id=org, order_id=oid, client_id=cid, system="five_point")
    db.add(fb)
    await db.commit()
    await _set_status(db, org, "suspended")
    form = (await client.get(f"/api/feedback/public/{fb.token}")).json()
    assert form["accepting"] is False and form["message"] == PUBLIC_OFF_MESSAGE
    r = await client.post(f"/api/feedback/public/{fb.token}", json={"rating": 5})
    assert r.status_code == 403 and r.json()["detail"] == PUBLIC_OFF_MESSAGE
    await db.refresh(fb)
    assert fb.submitted_at is None

    await _set_status(db, org, "active")
    assert (await client.get(f"/api/feedback/public/{fb.token}")).json()["accepting"] is True
    assert (await client.post(f"/api/feedback/public/{fb.token}", json={"rating": 5})).status_code == 200


# ── 3. Backlog on resume ──────────────────────────────────────────────────────────────────────
async def test_messages_that_waited_out_a_long_suspension_are_skipped_not_sent_late(client, tenant, db):
    from backend.models import NotificationLog
    from backend.services import notification_rules as nr
    from backend.services.dates import now_utc
    org = await org_of(client, tenant)
    now = now_utc()

    def row(age):
        return NotificationLog(org_id=org, scope="client", channel="email", trigger_event="manual",
                               recipient="x@example.invalid", subject="s", body="b",
                               send_at=now - age, status="scheduled")
    stale, fresh = row(timedelta(days=7)), row(timedelta(hours=2))
    db.add_all([stale, fresh])
    await db.commit()
    await _set_status(db, org, "suspended")
    assert (await nr.run_due(db, org_id=org))["sent"] == 0
    await _set_status(db, org, "active")                       # a week later: resumed

    res = await nr.run_due(db, org_id=org)
    assert res["sent"] == 1
    await db.refresh(stale)
    await db.refresh(fresh)
    assert fresh.status == "sent"
    assert stale.status == "skipped" and "late" in (stale.error or "").lower()
    assert not stale.attempts                                   # never handed to the provider


async def test_stale_cutoff_outlasts_the_whole_retry_ladder():
    """A message still inside its normal retries is never cut off as stale."""
    from backend.services import notification_rules as nr
    ladder = timedelta(seconds=sum(nr.RETRY_SCHEDULE_S)) + nr.STUCK_SENDING_AFTER * nr.OUTBOX_MAX_ATTEMPTS
    assert nr.MAX_SEND_LATENESS > ladder
