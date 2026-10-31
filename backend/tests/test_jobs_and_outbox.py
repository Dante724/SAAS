"""Platform: background work is safe with several workers and never runs ahead of the commit.

Regressions for audit findings P2 and P4:
- every uvicorn worker ran every periodic sweep (duplicate SMS/e-mail/webhooks per worker), and
  the notification sweep committed once at the end, so a crash mid-sweep re-sent everything;
- customer notifications and outbound webhooks were sent inside the request, before the commit,
  so a rolled-back change still notified customers and subscribers.
"""
import asyncio
import socket
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.core import jobs
from backend.models import NotificationLog, Order, Organisation, WebhookEndpoint
from backend.services import notification_rules as nr
from backend.services import webhooks as wh
from backend.services.dates import now_utc
from backend.tests.test_money_hardening import _order_with_total


@pytest.mark.asyncio
async def test_run_exclusive_runs_on_sqlite_and_lock_keys_are_stable():
    ran = []

    async def job():
        ran.append(1)
    assert await jobs.run_exclusive("x", job) is True and ran == [1]
    assert jobs._lock_key("a") == jobs._lock_key("a") != jobs._lock_key("b")


@pytest.mark.asyncio
async def test_notification_sweep_commits_each_send_and_never_resends(client, tenant, db, monkeypatch):
    org_id = (await db.execute(select(Organisation))).scalars().first().id
    due = now_utc() - timedelta(minutes=1)
    for to in ("+15550001", "+15550002"):
        db.add(NotificationLog(org_id=org_id, channel="sms", recipient=to, body="hi",
                               status="scheduled", send_at=due))
    await db.commit()
    calls = []

    async def flaky_send(_db, _org, _channel, *, recipient, subject, body, log=None):
        calls.append(recipient)
        if len(calls) == 2:
            raise RuntimeError("worker crashed mid-sweep")
        return {"status": "sent"}
    monkeypatch.setattr(nr, "_send", flaky_send)
    with pytest.raises(RuntimeError):
        await nr.run_due(db, org_id=org_id)
    await db.rollback()
    await nr.run_due(db, org_id=org_id)            # the next tick
    assert len(calls) == 2, calls                  # the first message was NOT sent again
    db.expire_all()
    statuses = sorted(s for (s,) in (await db.execute(select(NotificationLog.status).where(
        NotificationLog.org_id == org_id))).all())
    assert statuses == ["sending", "sent"]         # crashed one parked, never duplicated


@pytest.fixture
def capture_posts(monkeypatch):
    # Fake DNS for the webhook host only: faking every lookup also sent the worker's new
    # Postgres connection ("localhost") to a public address, so it timed out on Postgres.
    real_lookup = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, p, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", p))]
                        if h == "hooks.example.com" else real_lookup(h, p, *a, **k))
    posts = []
    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(lambda r: posts.append(r) or httpx.Response(200))
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return posts


async def _drain():
    for _ in range(50):
        if not jobs.job_runner._tasks:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_webhooks_are_sent_only_after_commit(client, tenant, db, capture_posts):
    org_id = (await db.execute(select(Organisation))).scalars().first().id
    db.add(WebhookEndpoint(org_id=org_id, url="https://hooks.example.com/in", events="*", active=True))
    await db.commit()

    await wh.emit_event(db, org_id, "order.created", {"id": "rolled-back"})
    assert capture_posts == []                      # nothing inline
    await db.rollback()
    await _drain()
    assert capture_posts == []                      # rolled back -> never sent

    await wh.emit_event(db, org_id, "order.created", {"id": "kept"})
    await db.commit()
    await _drain()
    assert len(capture_posts) == 1 and b"kept" in capture_posts[0].content


@pytest.mark.asyncio
async def test_status_notifications_fire_after_the_change_is_committed(client, tenant, db, monkeypatch):
    _cid, oid = await _order_with_total(client, tenant, 10)
    seen = []
    probe = async_sessionmaker(db.bind, expire_on_commit=False)

    async def fake_evaluate(_db, *, org_id, order_id, event, status_key=None, **kw):
        async with probe() as s:                     # an independent reader sees only committed data
            seen.append((await s.execute(select(Order.status).where(Order.id == order_id))).scalar_one())
        return {"matched": 1}
    monkeypatch.setattr(nr, "evaluate", fake_evaluate)
    r = await client.patch(f"/api/orders/{oid}/status", json={"status": "done"}, headers=tenant)
    assert r.status_code in (200, 201), r.text
    assert seen == ["done"], seen
