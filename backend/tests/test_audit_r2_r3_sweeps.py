"""Audit 29 Oct 2026.

R2: in the notification sweep one shop's database error left the shared session needing a
    rollback, so every later shop that tick failed too. Each shop now rolls back on its own.
R3: a crash mid-send left a webhook delivery at "sending" forever, and failed deliveries were
    retried every sweep with no backoff. Stale sends are reclaimed and retries wait longer each time.
"""
import socket
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from backend.core import scheduled
from backend.models import AuditLog, WebhookDelivery, WebhookEndpoint

PUBLIC = "93.184.216.34"


async def _org(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def _second_org(client):
    r = await client.post("/api/auth/register", json={
        "org_name": "Second Co", "name": "B", "email": "r2-second@test.dev", "password": "pw-test-12345"})
    assert r.status_code == 201, r.text
    return r.json()["org_id"]


@pytest.mark.asyncio
async def test_one_shops_db_error_does_not_stop_the_others(client, tenant, monkeypatch):
    from backend.services import notification_rules as nr
    orgs = {await _org(client, tenant), await _second_org(client)}
    state = {"failed": None, "ran": []}

    async def queue_reminders(db, *, org_id):
        if state["failed"] is None:          # whichever shop comes first breaks its flush
            state["failed"] = org_id
            db.add(AuditLog(org_id=None, action="x", entity_type="x"))
            await db.flush()
        return 0

    async def queue_appointment_reminders(db, *, org_id):
        await db.execute(select(AuditLog.id).limit(1))   # needs a usable session
        return 0

    async def run_due(db, *, org_id, now=None):
        state["ran"].append(org_id)
        return {"sent": 0}

    monkeypatch.setattr(nr, "queue_reminders", queue_reminders)
    monkeypatch.setattr(nr, "queue_appointment_reminders", queue_appointment_reminders)
    monkeypatch.setattr(nr, "run_due", run_due)
    await scheduled.sweep_notifications()
    assert state["failed"] in orgs
    assert set(state["ran"]) == orgs - {state["failed"]}


# ── R3 ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def net(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port))])
    seen, codes = [], []
    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(
                lambda r: (seen.append(r), httpx.Response(codes.pop(0) if codes else 200))[1])
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return seen, codes


async def _delivery(db, org_id, **kw):
    ep = WebhookEndpoint(org_id=org_id, url="https://hooks.example.com/in", events="*")
    db.add(ep)
    await db.flush()
    d = WebhookDelivery(org_id=org_id, endpoint_id=ep.id, event="order.created",
                        payload='{"event": "order.created"}', **kw)
    db.add(d)
    await db.commit()
    return d


async def _reload(db, d):
    did = d.id
    db.expire_all()
    return (await db.execute(select(WebhookDelivery).where(WebhookDelivery.id == did))).scalar_one()


@pytest.mark.asyncio
async def test_stale_sending_delivery_is_reclaimed(client, tenant, db, net):
    seen, _ = net
    d = await _delivery(db, await _org(client, tenant), status="sending", attempts=1,
                        claimed_at=datetime.utcnow() - timedelta(hours=1))
    await scheduled.retry_failed_webhooks()
    d = await _reload(db, d)
    assert d.status == "sent" and len(seen) == 1
    assert d.attempts == 3          # the interrupted send counts, plus this one


@pytest.mark.asyncio
async def test_fresh_sending_delivery_is_left_alone(client, tenant, db, net):
    seen, _ = net
    d = await _delivery(db, await _org(client, tenant), status="sending", attempts=0,
                        claimed_at=datetime.utcnow())
    await scheduled.retry_failed_webhooks()
    assert (await _reload(db, d)).status == "sending" and seen == []


@pytest.mark.asyncio
async def test_failed_delivery_waits_before_the_next_try(client, tenant, db, net):
    seen, codes = net
    d = await _delivery(db, await _org(client, tenant), status="pending", attempts=0)
    codes.extend([500, 500])
    await scheduled.retry_failed_webhooks()
    d = await _reload(db, d)
    assert d.status == "failed" and d.attempts == 1
    assert d.next_attempt_at and d.next_attempt_at > datetime.utcnow()
    first_wait = d.next_attempt_at - datetime.utcnow()

    await scheduled.retry_failed_webhooks()           # too soon: skipped
    assert len(seen) == 1

    d.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()
    await scheduled.retry_failed_webhooks()
    d = await _reload(db, d)
    assert len(seen) == 2 and d.attempts == 2
    assert d.next_attempt_at - datetime.utcnow() > first_wait   # waits longer each time


@pytest.mark.asyncio
async def test_claim_records_when_the_send_started(client, tenant, db):
    from backend.services.webhooks import claim_delivery
    d = await _delivery(db, await _org(client, tenant), status="pending", attempts=0)
    assert await claim_delivery(db, d, ("pending",))
    d = await _reload(db, d)
    assert d.status == "sending" and d.claimed_at is not None
