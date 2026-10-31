"""Tekly Go create/finish flows: the server enforces what the phone app hides.

- estimates need can_create_estimates; finishing a job needs can_close_tickets;
  payments need can_take_payments and can't exceed the balance; pay links need
  can_create_payment_links
- an Idempotency-Key makes a retried create or payment run once
- tasks: assigning other people needs can_assign_staff; only the people on a task change it
- photos can be appended without wiping what's already there
"""
import uuid

import pytest
from sqlalchemy import func, select

from backend.tests.test_sensitive_reads import _restricted


async def _order_with_total(client, h, total=100):
    cid = (await client.post("/api/clients", json={"name": "Go C", "phone": f"+1555{uuid.uuid4().int % 10**7}"}, headers=h)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "complaint": "screen"}, headers=h)).json()["id"]
    await client.post(f"/api/orders/{oid}/lines", json={
        "line_type": "labour", "description": "Fix", "qty": 1, "unit_price": total}, headers=h)
    return oid


async def _card_account(client, h):
    await client.post("/api/cash/accounts", json={"name": "GoCard", "payment_option": "card"}, headers=h)
    return [a for a in (await client.get("/api/cash/accounts", headers=h)).json()["items"] if a["name"] == "GoCard"][0]["id"]


async def _terminal_status(client, h):
    items = (await client.get("/api/statuses?record_type=work_order", headers=h)).json()["items"]
    return next(s["key"] for s in items if s.get("is_terminal"))


# ── permissions ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_estimates_need_their_own_permission(client, db, tenant):
    h, _, _ = await _restricted(client, db, ["can_create_estimates"])
    r = await client.post("/api/orders", headers=h, json={"complaint": "quote", "is_estimate": True})
    assert r.status_code == 403 and "can_create_estimates" in r.json()["detail"]
    r = await client.post("/api/orders", headers=h, json={"complaint": "job"})
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_finishing_a_job_needs_can_close_tickets(client, db, tenant):
    oid = await _order_with_total(client, tenant, 0)
    done = await _terminal_status(client, tenant)
    h, _, _ = await _restricted(client, db, ["can_close_tickets"])
    r = await client.patch(f"/api/orders/{oid}/status", headers=h, json={"status": done})
    assert r.status_code == 403 and "finish" in r.json()["detail"]
    assert (await client.patch(f"/api/orders/{oid}/status", headers=tenant, json={"status": done})).status_code == 200


@pytest.mark.asyncio
async def test_payments_need_can_take_payments_not_finance(client, db, tenant):
    oid = await _order_with_total(client, tenant, 100)
    aid = await _card_account(client, tenant)
    h, _, _ = await _restricted(client, db, ["can_take_payments"])
    r = await client.post(f"/api/cash/orders/{oid}/pay", headers=h, json={"method": "card", "account_id": aid, "amount": 10})
    assert r.status_code == 403
    # someone who may take payments but not use finance can still see what's owed and pay it
    h2, _, _ = await _restricted(client, db, ["can_view_finance"])
    ctx = await client.get(f"/api/cash/orders/{oid}/payment-context", headers=h2)
    assert ctx.status_code == 200 and ctx.json()["remaining"] == pytest.approx(100)
    r = await client.post(f"/api/cash/orders/{oid}/pay", headers=h2, json={"method": "card", "account_id": aid, "amount": 100})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_payment_cannot_exceed_balance(client, tenant):
    oid = await _order_with_total(client, tenant, 50)
    aid = await _card_account(client, tenant)
    r = await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant, json={"method": "card", "account_id": aid, "amount": 500})
    assert r.status_code == 400 and "more than is owed" in r.json()["detail"]


@pytest.mark.asyncio
async def test_pay_links_need_can_create_payment_links(client, db, tenant):
    oid = await _order_with_total(client, tenant, 30)
    h, _, _ = await _restricted(client, db, ["can_create_payment_links"])
    r = await client.post(f"/api/orders/{oid}/payment-link", headers=h)
    assert r.status_code == 403


# ── idempotency ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retried_create_with_same_key_makes_one_job(client, db, tenant):
    from backend.models import Order
    before = (await db.execute(select(func.count(Order.id)))).scalar()
    key = {"Idempotency-Key": f"k-{uuid.uuid4()}"}
    r1 = await client.post("/api/orders", headers={**tenant, **key}, json={"complaint": "twice?"})
    r2 = await client.post("/api/orders", headers={**tenant, **key}, json={"complaint": "twice?"})
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert (await db.execute(select(func.count(Order.id)))).scalar() == before + 1
    # a different key is a different job
    r3 = await client.post("/api/orders", headers={**tenant, "Idempotency-Key": f"k-{uuid.uuid4()}"}, json={"complaint": "new"})
    assert r3.json()["id"] != r1.json()["id"]


@pytest.mark.asyncio
async def test_retried_part_payment_posts_once(client, db, tenant):
    from backend.models import Payment
    oid = await _order_with_total(client, tenant, 100)
    aid = await _card_account(client, tenant)
    key = {"Idempotency-Key": f"p-{uuid.uuid4()}"}
    body = {"method": "card", "account_id": aid, "amount": 40}
    r1 = await client.post(f"/api/cash/orders/{oid}/pay", headers={**tenant, **key}, json=body)
    r2 = await client.post(f"/api/cash/orders/{oid}/pay", headers={**tenant, **key}, json=body)
    assert r1.status_code == 200 and r2.json() == r1.json()
    n = (await db.execute(select(func.count(Payment.id)).where(Payment.order_id == oid))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_failed_request_frees_its_key(client, tenant):
    """A refused request doesn't burn the key: fixing the input and retrying with it works."""
    oid = await _order_with_total(client, tenant, 20)
    aid = await _card_account(client, tenant)
    key = {"Idempotency-Key": f"f-{uuid.uuid4()}"}
    bad = await client.post(f"/api/cash/orders/{oid}/pay", headers={**tenant, **key}, json={"method": "card", "account_id": aid, "amount": 999})
    assert bad.status_code == 400
    ok = await client.post(f"/api/cash/orders/{oid}/pay", headers={**tenant, **key}, json={"method": "card", "account_id": aid, "amount": 20})
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_key_cannot_be_reused_for_another_action(client, tenant):
    key = {"Idempotency-Key": f"x-{uuid.uuid4()}"}
    assert (await client.post("/api/clients", headers={**tenant, **key}, json={"name": "Keyed"})).status_code == 201
    r = await client.post("/api/orders", headers={**tenant, **key}, json={"complaint": "x"})
    assert r.status_code == 422


# ── tasks ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_assignment_and_ownership(client, db, tenant):
    h, emp, owner = await _restricted(client, db, ["can_assign_staff"])
    # may take a task themselves, not give one to someone else
    mine = await client.post("/api/tasks", headers=h, json={"title": "Order part", "assigned_to_id": emp.id})
    assert mine.status_code == 201
    r = await client.post("/api/tasks", headers=h, json={"title": "Do this", "assigned_to_id": owner.id})
    assert r.status_code == 403
    # a task between other people can't be changed or deleted by them
    other = (await client.post("/api/tasks", headers=tenant, json={"title": "Owner's", "assigned_to_id": owner.id})).json()
    assert (await client.patch(f"/api/tasks/{other['id']}", headers=h, json={"status": "done"})).status_code == 403
    assert (await client.delete(f"/api/tasks/{other['id']}", headers=h)).status_code == 403
    # their own task they can complete
    assert (await client.patch(f"/api/tasks/{mine.json()['id']}", headers=h, json={"status": "done"})).status_code == 200


@pytest.mark.asyncio
async def test_my_tasks_include_ones_shared_with_me(client, db, tenant):
    h, emp, owner = await _restricted(client, db, [])
    t = (await client.post("/api/tasks", headers=tenant, json={
        "title": "Two techs", "assigned_to_id": owner.id, "assignee_ids": [owner.id, emp.id]})).json()
    ids = [x["id"] for x in (await client.get("/api/tasks?mine=true", headers=h)).json()["items"]]
    assert t["id"] in ids


# ── photos ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_photo_append_keeps_existing_photos(client, tenant):
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    a = (await client.post("/api/media", headers=tenant, json={"data": png})).json()["id"]
    b = (await client.post("/api/media", headers=tenant, json={"data": png})).json()["id"]
    oid = (await client.post("/api/orders", headers=tenant, json={"complaint": "pics", "intake_photos": [a]})).json()["id"]
    r = await client.post(f"/api/orders/{oid}/intake-photos/add", headers=tenant, json={"photos": [b]})
    assert r.status_code == 200
    assert [p["id"] for p in r.json()["intake_photos"]] == [a, b]
    # adding the same one again changes nothing (safe to retry)
    r2 = await client.post(f"/api/orders/{oid}/intake-photos/add", headers=tenant, json={"photos": [b]})
    assert [p["id"] for p in r2.json()["intake_photos"]] == [a, b]


# ── migration gc144g1: nobody's access changes ───────────────────────────────

def test_migration_keeps_everyones_access():
    from backend.services.permissions import grant_go_codes, role_allows

    class R:
        def __init__(self, perms, explicit):
            self.permissions, self.perms_explicit = perms, explicit

    # explicit role with finance: can close, take payments and send links, as before
    p, ch = grant_go_codes({"can_view_finance": True, "can_create_tickets": True}, True)
    r = R(p, True)
    assert ch and all(role_allows(r, c) for c in ("can_close_tickets", "can_take_payments", "can_create_payment_links", "can_create_estimates"))
    # explicit technician without finance: can still close, but not take payments or send links
    p, _ = grant_go_codes({"can_view_finance": False, "can_create_tickets": True, "can_create_payment_links": True}, True)
    r = R(p, True)
    assert role_allows(r, "can_close_tickets") and not role_allows(r, "can_take_payments")
    assert not role_allows(r, "can_create_payment_links")
    # an explicit role that already had estimates switched off keeps it off
    p, _ = grant_go_codes({"can_create_tickets": True, "can_create_estimates": False}, True)
    assert not role_allows(R(p, True), "can_create_estimates")
    # legacy role that denied finance: the new money codes don't default to allowed
    p, _ = grant_go_codes({"can_view_finance": False}, False)
    r = R(p, False)
    assert not role_allows(r, "can_take_payments") and role_allows(r, "can_close_tickets")
    # legacy role with nothing denied, and the admin role: untouched
    assert grant_go_codes({}, False) == ({}, False)
    assert grant_go_codes({"all": "write"}, False) == ({"all": "write"}, False)
