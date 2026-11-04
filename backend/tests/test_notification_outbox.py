"""Customer notifications are a real outbox (architecture hardening: notification delivery).

Before: a rule sent its email/SMS inside the HTTP request, a provider failure was stored as
"failed" and never retried, the order status endpoint swallowed every notification error with
`except Exception: pass`, and reset/verification emails were fire-and-forget. A Twilio or SMTP
blip meant the customer silently never got "ready for pickup" or a password reset.

Now: evaluating a rule only queues the message (status "scheduled", same transaction as the
change); the sweep claims each due row with a compare-and-set, retries transient errors with
backoff, marks a row "dead" after OUTBOX_MAX_ATTEMPTS or on a permanent error, and reclaims rows
stuck in "sending". All providers here are fakes: nothing talks to Twilio or SMTP.
"""
import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from backend.models import Client, NotificationLog, NotificationRule
from backend.services import notification_rules as nr
from backend.services.dates import now_utc


class FakeProvider:
    """Stands in for Twilio/SMTP. `script` is a list of results handed out in order; the last
    one repeats. A result that is an Exception is raised (a crashed provider call)."""

    def __init__(self, *script, delay: float = 0):
        self.script = list(script) or [{"status": "sent", "error": None}]
        self.calls = []
        self.delay = delay

    def adapter_for(self, channel, creds=None):
        provider = self

        class _Adapter:
            name = channel

            async def send(self, *, recipient, subject, body, html=None, headers=None):
                provider.calls.append(recipient)
                if provider.delay:
                    await asyncio.sleep(provider.delay)
                i = min(len(provider.calls) - 1, len(provider.script) - 1)
                res = provider.script[i]
                if isinstance(res, Exception):
                    raise res
                return dict(res)
        return _Adapter()


OK = {"status": "sent", "error": None}
BLIP = {"status": "failed", "error": "Twilio 503: Service Unavailable"}
BAD_NUMBER = {"status": "failed",
              "error": 'Twilio 400: {"code": 21211, "message": "The \'To\' number +1555 is not a valid phone number."}'}


@pytest.fixture
def provider(monkeypatch):
    p = FakeProvider(OK)
    monkeypatch.setattr(nr, "_adapter_for", p.adapter_for)
    return p


async def _org_id(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _due_sms(db, org_id, *, minutes_ago=1, **kw):
    cust = Client(org_id=org_id, name="Pat", phone="+15550101", consent_phone=True)
    db.add(cust)
    await db.flush()
    log = NotificationLog(org_id=org_id, scope="client", channel="sms", trigger_event="status_changed",
                          client_id=cust.id, recipient=cust.phone, body="Your phone is ready",
                          status="scheduled", send_at=now_utc() - timedelta(minutes=minutes_ago), **kw)
    db.add(log)
    await db.commit()
    return log.id


async def _row(db, log_id):
    db.expire_all()
    return (await db.execute(select(NotificationLog).where(NotificationLog.id == log_id))).scalar_one()


# ── retry ────────────────────────────────────────────────────────────────────────────────────
async def test_a_provider_blip_is_retried_and_sent_on_the_second_attempt(client, tenant, db, provider):
    provider.script = [BLIP, OK]
    org = await _org_id(client, tenant)
    lid = await _due_sms(db, org)
    t0 = now_utc()

    first = await nr.run_due(db, org_id=org, now=t0)
    row = await _row(db, lid)
    assert first["sent"] == 0 and row.status == "scheduled"          # still owed, not "failed"
    assert row.attempts == 1 and row.last_error and "503" in row.last_error
    assert row.next_attempt_at is not None and row.next_attempt_at > t0

    await nr.run_due(db, org_id=org, now=t0 + timedelta(seconds=10))   # backoff not over yet
    assert len(provider.calls) == 1

    again = await nr.run_due(db, org_id=org, now=row.next_attempt_at + timedelta(seconds=1))
    row = await _row(db, lid)
    assert again["sent"] == 1 and row.status == "sent" and row.attempts == 2
    assert len(provider.calls) == 2


async def test_persistent_failure_ends_dead_after_max_attempts(client, tenant, db, provider):
    provider.script = [BLIP]
    org = await _org_id(client, tenant)
    lid = await _due_sms(db, org)
    when = now_utc()
    for _ in range(nr.OUTBOX_MAX_ATTEMPTS + 3):         # more ticks than attempts allowed
        await nr.run_due(db, org_id=org, now=when)
        row = await _row(db, lid)
        when = (row.next_attempt_at or when) + timedelta(seconds=1)
    row = await _row(db, lid)
    assert len(provider.calls) == nr.OUTBOX_MAX_ATTEMPTS
    assert row.status == "dead" and row.attempts == nr.OUTBOX_MAX_ATTEMPTS
    assert "503" in (row.last_error or "") and row.error           # the history shows why
    assert row.next_attempt_at is None


async def test_a_permanent_error_goes_straight_to_dead(client, tenant, db, provider):
    provider.script = [BAD_NUMBER]
    org = await _org_id(client, tenant)
    lid = await _due_sms(db, org)
    await nr.run_due(db, org_id=org)
    row = await _row(db, lid)
    assert row.status == "dead" and row.attempts == 1 and "21211" in row.last_error
    await nr.run_due(db, org_id=org, now=now_utc() + timedelta(days=2))
    assert len(provider.calls) == 1


def test_error_classification():
    assert nr.is_permanent_error("Twilio 400: {\"code\": 21610, \"message\": \"unsubscribed\"}")
    assert nr.is_permanent_error("No phone number on file")
    assert nr.is_permanent_error("No client consent for sms")
    assert nr.is_permanent_error("SMTP: {'nobody@x.test': (550, b'5.1.1 User unknown')}")
    assert not nr.is_permanent_error("Twilio 503: Service Unavailable")
    assert not nr.is_permanent_error("Twilio 429: Too Many Requests")
    assert not nr.is_permanent_error("Twilio 401: Authenticate")         # creds can be fixed
    assert not nr.is_permanent_error("SMTP: [Errno 61] Connection refused")
    assert not nr.is_permanent_error("Twilio: ReadTimeout")
    assert not nr.is_permanent_error(None)


# ── stuck rows ───────────────────────────────────────────────────────────────────────────────
async def test_a_row_stuck_in_sending_is_reclaimed_and_sent(client, tenant, db, provider):
    org = await _org_id(client, tenant)
    t0 = now_utc()
    stuck = await _due_sms(db, org, minutes_ago=30)
    fresh = await _due_sms(db, org, minutes_ago=30)
    row = await _row(db, stuck)
    row.status, row.claimed_at = "sending", t0 - nr.STUCK_SENDING_AFTER - timedelta(minutes=1)
    await db.commit()
    row2 = await _row(db, fresh)
    row2.status, row2.claimed_at = "sending", t0 - timedelta(seconds=30)   # a send in progress
    await db.commit()

    res = await nr.run_due(db, org_id=org, now=t0)
    row = await _row(db, stuck)
    assert res["sent"] == 1 and row.status == "sent"
    assert row.attempts == 2                       # the interrupted try counts as one
    assert (await _row(db, fresh)).status == "sending"   # in-flight sends are left alone
    assert len(provider.calls) == 1


# ── concurrency ──────────────────────────────────────────────────────────────────────────────
async def test_two_concurrent_sweeps_send_a_row_exactly_once(client, tenant, db, monkeypatch):
    """Two sweeps (e.g. two workers, or the sweep and the manual "run due" button) racing over
    the same due rows: the compare-and-set claim lets exactly one of them send each row."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    slow = FakeProvider(OK, delay=0.05)          # slow enough that the two sweeps overlap
    monkeypatch.setattr(nr, "_adapter_for", slow.adapter_for)
    org = await _org_id(client, tenant)
    ids = [await _due_sms(db, org) for _ in range(4)]
    Session = async_sessionmaker(db.bind, expire_on_commit=False)

    async def sweep():
        async with Session() as s:
            return await nr.run_due(s, org_id=org)
    a, b = await asyncio.gather(sweep(), sweep())
    assert a["sent"] + b["sent"] == 4
    assert sorted(slow.calls) == ["+15550101"] * 4             # one send per row, no duplicates
    assert {(await _row(db, i)).status for i in ids} == {"sent"}


# ── status change with the provider down ─────────────────────────────────────────────────────
async def _status_setup(client, tenant, db):
    org = await _org_id(client, tenant)
    await db.execute(delete(NotificationRule).where(NotificationRule.org_id == org))
    db.add(NotificationRule(org_id=org, module="order", scope="client", event="status_changed",
                            channel="sms", active=True))
    await db.commit()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Sam", "phone": "+15550199"},
                             headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x"},
                             headers=tenant)).json()["id"]
    statuses = (await client.get("/api/statuses", headers=tenant)).json()["items"]
    key = next(s["key"] for s in statuses if not s.get("is_terminal") and s["key"] != "new")
    return org, oid, key


async def test_status_change_succeeds_and_queues_when_the_provider_is_down(client, tenant, db, monkeypatch):
    down = FakeProvider(ConnectionError("twilio unreachable"))
    monkeypatch.setattr(nr, "_adapter_for", down.adapter_for)
    org, oid, key = await _status_setup(client, tenant, db)

    r = await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=tenant)
    assert r.status_code == 200, r.text
    assert down.calls == []                              # the request never called the provider
    db.expire_all()
    logs = (await db.execute(select(NotificationLog).where(
        NotificationLog.order_id == oid, NotificationLog.trigger_event == "status_changed"))).scalars().all()
    assert [(lg.channel, lg.status) for lg in logs] == [("sms", "scheduled")]

    up = FakeProvider(OK)                                # provider back: the sweep delivers it
    monkeypatch.setattr(nr, "_adapter_for", up.adapter_for)
    res = await nr.run_due(db, org_id=org)
    assert res["sent"] == 1 and up.calls == ["+15550199"]


async def test_a_rule_evaluation_error_is_logged_not_swallowed(client, tenant, db, monkeypatch, caplog):
    org, oid, key = await _status_setup(client, tenant, db)

    async def boom(*a, **kw):
        raise RuntimeError("template exploded")
    monkeypatch.setattr(nr, "evaluate", boom)
    with caplog.at_level(logging.ERROR):
        r = await client.patch(f"/api/orders/{oid}/status", json={"status": key}, headers=tenant)
    assert r.status_code == 200, r.text                  # the status change still happens
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).json()["status"] == key
    hits = [rec for rec in caplog.records if oid in rec.getMessage() and org in rec.getMessage()]
    assert hits and hits[0].exc_info, [rec.getMessage() for rec in caplog.records]


# ── history API ──────────────────────────────────────────────────────────────────────────────
async def test_history_shows_a_dead_message_as_failed(client, tenant, db, provider):
    provider.script = [BAD_NUMBER]
    org = await _org_id(client, tenant)
    lid = await _due_sms(db, org)
    await nr.run_due(db, org_id=org)
    items = (await client.get("/api/notifications/log", headers=tenant)).json()["items"]
    item = next(i for i in items if i["id"] == lid)
    assert item["status"] == "failed" and "21211" in item["error"]


# ── account emails ───────────────────────────────────────────────────────────────────────────
async def test_reset_email_is_retried_after_a_transient_failure(client, tenant, monkeypatch):
    from backend.routers import auth as auth_router
    calls = []

    async def flaky(to, subject, body, *a, **kw):
        calls.append(to)
        return {"status": "failed", "error": "SMTP: [Errno 61] Connection refused"} if len(calls) == 1 else OK
    monkeypatch.setattr(auth_router, "send_direct_email", flaky)
    monkeypatch.setattr(nr, "ACCOUNT_EMAIL_RETRY_S", (0, 0))
    email = (await client.get("/api/auth/me", headers=tenant)).json()["email"]
    r = await client.post("/api/auth/password/forgot", json={"email": email})
    assert r.status_code == 200
    for _ in range(3):                                    # the send runs after the reply
        await asyncio.gather(*auth_router._BACKGROUND, *nr._ACCOUNT_EMAIL_TASKS)
    assert calls == [email, email]


# ── the sweep ────────────────────────────────────────────────────────────────────────────────
def test_the_outbox_sweep_runs_at_least_once_a_minute():
    from backend.core import scheduled  # noqa: F401 — registers the jobs
    from backend.core.jobs import job_runner
    job = next(p for p in job_runner._periodics if p.name == "notification-outbox")
    assert job.interval_s <= 60


async def test_the_outbox_sweep_delivers_only_for_shops_with_due_messages(client, tenant, db, provider, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from backend.core import scheduled
    org = await _org_id(client, tenant)
    lid = await _due_sms(db, org)
    old = await _due_sms(db, org)
    row = await _row(db, old)
    row.status = "failed"                  # written before the outbox: history, never re-sent
    await db.commit()
    monkeypatch.setattr("backend.db.database.AsyncSessionLocal",
                        async_sessionmaker(db.bind, expire_on_commit=False))
    assert await nr.orgs_with_due(db) == [org]
    await scheduled.deliver_notifications()
    assert (await _row(db, lid)).status == "sent" and (await _row(db, old)).status == "failed"
    assert provider.calls == ["+15550101"]
    assert await nr.orgs_with_due(db) == []
