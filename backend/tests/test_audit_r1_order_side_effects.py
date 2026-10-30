"""Audit 29 Oct 2026, R1: the side effects of creating an order (upsells, checklists, webhook,
commissions, alerts, notifications, automations) each sat in `except: pass`. A failure vanished
without a log line, and a database error left the session broken so the hooks after it silently
did nothing. Each hook is now logged and rolled back on its own; the rest still run and commit."""
import logging

import pytest
from sqlalchemy import select, text

from backend.models import Task


async def _client_id(client, h):
    return (await client.post("/api/clients", json={"name": "C"}, headers=h)).json()["id"]


@pytest.mark.asyncio
async def test_a_failing_hook_is_logged_and_later_hooks_still_commit(client, tenant, db, monkeypatch, caplog):
    import backend.routers.forms as forms
    from backend.services import automation

    async def broken_upsells(db_, org_id, order, current):
        await db_.execute(text("SELECT * FROM no_such_table"))   # leaves the transaction failed

    async def automation_adds_task(db_, org_id, event, *, order=None, part=None):
        db_.add(Task(org_id=org_id, title="Call the customer", order_id=order.id, status="open"))
        return {"matched": 1, "fired": 1}

    monkeypatch.setattr(forms, "sync_order_field_upsells", broken_upsells)
    monkeypatch.setattr(automation, "run", automation_adds_task)
    cid = await _client_id(client, tenant)
    with caplog.at_level(logging.ERROR):
        r = await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "fix"})
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]

    assert any("upsell" in rec.getMessage() and rec.levelno >= logging.ERROR for rec in caplog.records), \
        [rec.getMessage() for rec in caplog.records]
    tasks = (await db.execute(select(Task).where(Task.order_id == oid))).scalars().all()
    assert [t.title for t in tasks] == ["Call the customer"]


@pytest.mark.asyncio
async def test_failing_notifications_are_logged_not_fatal(client, tenant, monkeypatch, caplog):
    from backend.services import notification_rules, notifications

    async def boom(*a, **kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notification_rules, "evaluate", boom)
    monkeypatch.setattr(notifications, "dispatch_order_notifications", boom)
    cid = await _client_id(client, tenant)
    with caplog.at_level(logging.ERROR):
        r = await client.post("/api/orders", headers=tenant, json={"client_id": cid, "complaint": "fix"})
    assert r.status_code in (200, 201), r.text
    assert any("notification" in rec.getMessage() for rec in caplog.records)
