"""Hardening council (High): a free-trial shop could send unlimited SMS/WhatsApp to arbitrary
numbers through the operator's shared Twilio account (toll fraud / SMS pumping).

POST /api/chats/threads takes any contact_phone and POST /threads/{id}/send had no limit;
services/notifications._adapter_for() falls back to settings.TWILIO_* when the shop has no own
Twilio keys. Fix: a per-shop daily cap on texts sent through the OPERATOR's credentials
(OPERATOR_SMS_DAILY_CAP, lower for trial shops), enforced at the one send seam (TwilioAdapter)
via services.ratelimit (Redis-backed when configured). Over the cap the send is refused with a
clear message; a shop on its own Twilio account is never capped.
"""
import httpx
import pytest
from sqlalchemy import select

from backend.config import settings
from backend.models import Organisation
from backend.services import notifications
from backend.services.twilio_voice import TEST_FROM_NUMBER

pytestmark = pytest.mark.asyncio

OPERATOR_SID = "ACoperator000000000000000000000000"


class _FakeTwilio:
    """Counts what would have been billed to Twilio."""
    sent: list[str] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, auth=None):
        _FakeTwilio.sent.append(auth[0])
        return httpx.Response(201, json={"sid": "SM1"})


@pytest.fixture
def operator_twilio(monkeypatch):
    """The operator's shared Twilio keys are configured on the server; nothing reaches the network."""
    _FakeTwilio.sent = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeTwilio)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", OPERATOR_SID)
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "operator-token")
    monkeypatch.setattr(settings, "TWILIO_FROM", TEST_FROM_NUMBER)   # the S7 live guard lets this through
    monkeypatch.setattr(settings, "OPERATOR_SMS_DAILY_CAP", 5)
    monkeypatch.setattr(settings, "OPERATOR_SMS_DAILY_CAP_TRIAL", 3)
    return _FakeTwilio


async def _thread(client, h, phone="+15550001000"):
    r = await client.post("/api/chats/threads", json={"channel": "sms", "contact_phone": phone}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _send(client, h, tid, body="hi"):
    return await client.post(f"/api/chats/threads/{tid}/send", json={"body": body}, headers=h)


async def test_trial_shop_is_capped_on_the_operator_account(client, tenant, operator_twilio):
    tid = await _thread(client, tenant)
    for _ in range(3):
        r = await _send(client, tenant, tid)
        assert r.status_code == 201 and r.json()["status"] == "sent", r.text
    r = await _send(client, tenant, tid)
    assert r.status_code == 429, r.text
    assert "Daily text limit" in r.json()["detail"] and "own Twilio" in r.json()["detail"]
    # A fresh thread to another number is the same budget.
    other = await _thread(client, tenant, "+15550002000")
    assert (await _send(client, tenant, other)).status_code == 429
    assert len(operator_twilio.sent) == 3
    # The refusal is not recorded as a sent message.
    msgs = (await client.get(f"/api/chats/threads/{tid}/messages", headers=tenant)).json()
    assert sum(1 for m in msgs["items"] if m["direction"] == "out") == 3


async def test_paying_shop_gets_the_full_cap(client, tenant, operator_twilio, db):
    org = (await db.execute(select(Organisation))).scalars().first()
    org.sub_status = "active"
    await db.commit()
    tid = await _thread(client, tenant)
    for _ in range(5):
        assert (await _send(client, tenant, tid)).status_code == 201
    assert (await _send(client, tenant, tid)).status_code == 429
    assert len(operator_twilio.sent) == 5


async def test_shop_with_its_own_twilio_account_is_not_capped(client, tenant, operator_twilio):
    r = await client.put("/api/integrations/credentials", headers=tenant, json={"provider": "twilio", "data": {
        "account_sid": "ACownshop00000000000000000000000000", "auth_token": "own", "from": TEST_FROM_NUMBER}})
    assert r.status_code == 200, r.text
    tid = await _thread(client, tenant)
    for _ in range(8):
        r = await _send(client, tenant, tid)
        assert r.status_code == 201 and r.json()["status"] == "sent", r.text
    assert len(operator_twilio.sent) == 8 and set(operator_twilio.sent) == {"ACownshop00000000000000000000000000"}


async def test_automatic_notifications_degrade_to_a_failed_log_not_a_crash(client, tenant, operator_twilio, db):
    """Status-update notifications share the seam: over the cap the adapter reports a failed send
    with the human message instead of raising into the order flow."""
    org = (await db.execute(select(Organisation))).scalars().first()
    creds = await notifications.notif_creds(db, org.id, "sms")
    adapter = notifications._adapter_for("sms", creds)
    for _ in range(3):
        assert (await adapter.send(recipient="+15550003000", subject=None, body="x"))["status"] == "sent"
    res = await adapter.send(recipient="+15550003000", subject=None, body="x")
    assert res["status"] == "failed" and "Daily text limit" in res["error"]
    assert len(operator_twilio.sent) == 3


async def test_campaign_over_the_cap_is_refused(client, tenant, operator_twilio):
    for i in range(4):
        r = await client.post("/api/clients", json={"name": f"C{i}", "phone": f"+1555000{i:04d}"}, headers=tenant)
        assert r.status_code == 201, r.text
    cid = (await client.post("/api/comms/campaigns", json={"name": "Blast", "channel": "sms", "body": "Sale!"},
                             headers=tenant)).json()["id"]
    r = await client.post(f"/api/comms/campaigns/{cid}/send", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["recipients"] == 3 and "Daily text limit" in (r.json().get("error") or "")
    assert len(operator_twilio.sent) == 3
    # Budget spent: a second campaign today is refused outright.
    cid2 = (await client.post("/api/comms/campaigns", json={"name": "Again", "channel": "sms", "body": "x"},
                              headers=tenant)).json()["id"]
    r = await client.post(f"/api/comms/campaigns/{cid2}/send", headers=tenant)
    assert r.status_code == 429, r.text
