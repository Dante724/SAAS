"""Hardening council (High): a free-trial shop could send unlimited SMS/WhatsApp to arbitrary
numbers through the operator's shared Twilio account (toll fraud / SMS pumping).

POST /api/chats/threads takes any contact_phone and POST /threads/{id}/send had no limit;
services/notifications._adapter_for() falls back to settings.TWILIO_* when the shop has no own
Twilio keys. Fix: a per-shop daily cap on texts sent through the OPERATOR's credentials
(OPERATOR_SMS_DAILY_CAP, lower for trial shops), enforced at the one send seam (TwilioAdapter)
via services.ratelimit (Redis-backed when configured). Over the cap the send is refused with a
clear message; a shop on its own Twilio account is never capped.
"""
import asyncio
import os
from datetime import datetime, timezone

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
    monkeypatch.setattr(settings, "OPERATOR_SMS_RESERVE_PCT", 0)   # the reserve has its own tests below
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


# ── Independent review of #107 ───────────────────────────────────────────────────────────────

async def _org(db):
    return (await db.execute(select(Organisation))).scalars().first()


async def test_from_number_only_creds_still_go_through_the_capped_operator_account(client, tenant,
                                                                                   operator_twilio):
    """A shop that saves only its own `from` number has no Twilio account of its own: the texts are
    billed to the operator's account (the adapter falls back to the server SID/token), so the cap
    applies."""
    r = await client.put("/api/integrations/credentials", headers=tenant, json={"provider": "twilio", "data": {
        "from": TEST_FROM_NUMBER}})
    assert r.status_code == 200, r.text
    tid = await _thread(client, tenant)
    for _ in range(3):
        assert (await _send(client, tenant, tid)).status_code == 201
    r = await _send(client, tenant, tid)
    assert r.status_code == 429, r.text
    assert operator_twilio.sent == [OPERATOR_SID] * 3


async def test_cap_is_kept_in_the_database_not_in_process_memory(client, tenant, operator_twilio):
    """A restart (or a second worker) starts with empty process memory; the day's count must hold."""
    from backend.services import ratelimit
    tid = await _thread(client, tenant)
    for _ in range(3):
        assert (await _send(client, tenant, tid)).status_code == 201
    ratelimit._HITS.clear()            # what a restart or another worker sees
    assert (await _send(client, tenant, tid)).status_code == 429
    assert len(operator_twilio.sent) == 3


async def test_cap_resets_at_the_shops_midnight(client, tenant, operator_twilio, db, monkeypatch):
    """The budget is per day on the SHOP's clock: a shop at UTC+14 gets a fresh budget at its own
    midnight, not 24 hours after its first text."""
    from backend.services import dates
    org = await _org(db)
    org.timezone = "Pacific/Kiritimati"            # UTC+14
    await db.commit()
    tid = await _thread(client, tenant)
    now = {"t": datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)}   # 23:00 on 10 March there
    monkeypatch.setattr(dates, "_clock", lambda: now["t"])
    for _ in range(3):
        assert (await _send(client, tenant, tid)).status_code == 201
    assert (await _send(client, tenant, tid)).status_code == 429
    now["t"] = datetime(2026, 3, 10, 10, 30, tzinfo=timezone.utc)  # 00:30 on 11 March there
    r = await _send(client, tenant, tid)
    assert r.status_code == 201, r.text
    assert len(operator_twilio.sent) == 4


@pytest.mark.parametrize("status", ["past_due", "unpaid", "suspended", "trial_lost", "churned"])
async def test_only_an_active_subscription_gets_the_full_cap(client, tenant, operator_twilio, db, status):
    """A lapsed / unpaid / suspended shop is read-only in the app, but its scheduled texts (reminders)
    still run: they get the trial cap, not the paying one."""
    org = await _org(db)
    org.sub_status = status
    await db.commit()
    adapter = notifications._adapter_for("sms", await notifications.notif_creds(db, org.id, "sms"))
    got = [(await adapter.send(recipient="+15550003000", subject=None, body="x"))["status"] for _ in range(4)]
    assert got == ["sent", "sent", "sent", "failed"], status
    assert len(operator_twilio.sent) == 3


async def test_part_of_the_cap_is_kept_for_transactional_texts(client, tenant, operator_twilio, db, monkeypatch):
    """Campaigns and chats stop at cap - reserve; a job-status notification can still use the rest."""
    from backend.services import notification_rules
    monkeypatch.setattr(settings, "OPERATOR_SMS_DAILY_CAP", 10)
    monkeypatch.setattr(settings, "OPERATOR_SMS_RESERVE_PCT", 20)   # 2 of 10 kept back
    org = await _org(db)
    org.sub_status = "active"
    await db.commit()
    for i in range(7):
        r = await client.post("/api/clients", json={"name": f"C{i}", "phone": f"+1555100{i:04d}"}, headers=tenant)
        assert r.status_code == 201, r.text
    cid = (await client.post("/api/comms/campaigns", json={"name": "Blast", "channel": "sms", "body": "Sale!"},
                             headers=tenant)).json()["id"]
    r = await client.post(f"/api/comms/campaigns/{cid}/send", headers=tenant)
    assert r.status_code == 200 and r.json()["recipients"] == 7, r.text
    tid = await _thread(client, tenant)
    assert (await _send(client, tenant, tid)).status_code == 201       # 8 = cap - reserve
    assert (await _send(client, tenant, tid)).status_code == 429       # chats are not transactional
    # A status update to a customer still goes out, up to the full cap.
    for _ in range(2):
        res = await notification_rules._send(db, org.id, "sms", recipient="+15550009000", subject=None, body="Ready")
        assert res["status"] == "sent", res
    res = await notification_rules._send(db, org.id, "sms", recipient="+15550009000", subject=None, body="Ready")
    assert res["status"] == "failed" and "Daily text limit" in res["error"]
    assert len(operator_twilio.sent) == 10


async def test_birthday_and_win_back_texts_use_the_marketing_budget(client, tenant, operator_twilio, db, monkeypatch):
    from backend.models import NotificationLog
    from backend.services import notification_rules
    monkeypatch.setattr(settings, "OPERATOR_SMS_DAILY_CAP", 5)
    monkeypatch.setattr(settings, "OPERATOR_SMS_RESERVE_PCT", 40)   # 2 of 5 kept back
    org = await _org(db)
    org.sub_status = "active"
    await db.commit()
    results = []
    for event in ("birthday", "agent.win_back:c1", "birthday", "agent.win_back:c2"):
        log = NotificationLog(org_id=org.id, scope="client", channel="sms", trigger_event=event,
                              recipient="+15550009001", body="x")
        results.append((await notification_rules._send(db, org.id, "sms", recipient="+15550009001",
                                                       subject=None, body="x", log=log))["status"])
    assert results == ["sent", "sent", "sent", "failed"]
    assert len(operator_twilio.sent) == 3


_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")


@pytest.mark.skipif(not _URL.startswith("postgresql"), reason="Postgres: real parallel sessions")
async def test_parallel_sends_at_cap_minus_one_let_exactly_one_through(client, tenant, operator_twilio, db,
                                                                       monkeypatch):
    """N workers send at once with one text left in the day's budget: exactly one goes out."""
    from sqlalchemy.ext.asyncio import AsyncSession
    monkeypatch.setattr(settings, "OPERATOR_SMS_DAILY_CAP_TRIAL", 6)
    org = await _org(db)
    creds = await notifications.notif_creds(db, org.id, "sms")
    for _ in range(5):
        assert (await notifications._adapter_for("sms", creds).send(
            recipient="+15550004000", subject=None, body="x"))["status"] == "sent"
    await db.commit()

    async def one():
        async with AsyncSession(db.bind, expire_on_commit=False) as s:   # its own connection
            c = await notifications.notif_creds(s, org.id, "sms")
            res = await notifications._adapter_for("sms", c).send(recipient="+15550004000", subject=None, body="x")
            await s.commit()
            return res

    results = await asyncio.gather(*[one() for _ in range(12)])
    assert sum(1 for r in results if r["status"] == "sent") == 1, results
    assert sum(1 for r in results if r.get("capped")) == 11
    assert len(operator_twilio.sent) == 6


async def test_scheduled_win_back_texts_use_the_marketing_budget(client, tenant, operator_twilio, db, monkeypatch):
    """The worker tick (run_due) sends approved agent messages without their log: a win-back text
    still counts against the marketing budget, a payment reminder may use the reserve."""
    from backend.models import NotificationLog
    from backend.services import notification_rules, sense_agents
    monkeypatch.setattr(settings, "OPERATOR_SMS_DAILY_CAP", 5)
    monkeypatch.setattr(settings, "OPERATOR_SMS_RESERVE_PCT", 40)   # 2 of 5 kept back

    async def go(*a, **kw):
        return "go", None
    monkeypatch.setattr(sense_agents, "send_check", go)
    org = await _org(db)
    org.sub_status = "active"
    for i, ev in enumerate(["agent.win_back:a", "agent.win_back:b", "agent.win_back:c", "agent.win_back:d",
                            "agent.collections:e", "agent.collections:f"]):
        db.add(NotificationLog(org_id=org.id, scope="client", channel="sms", trigger_event=ev,
                               recipient=f"+1555000900{i}", body="x", status="scheduled",
                               send_at=datetime(2020, 1, 1)))
    await db.commit()
    await notification_rules.run_due(db, org_id=org.id)
    rows = {r.trigger_event: r.status for r in (await db.execute(select(NotificationLog).where(
        NotificationLog.org_id == org.id))).scalars()}
    assert sorted(rows.values()).count("sent") == 5, rows
    assert [rows[f"agent.win_back:{k}"] for k in "abcd"].count("sent") == 3, rows
    assert rows["agent.collections:e"] == rows["agent.collections:f"] == "sent", rows
