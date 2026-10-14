"""Work-order actions — Duplicate, Create PO, Delete(archive).

Covers the behaviour that was previously broken/stubbed: duplicate now copies the
lines, create-po raises a linked PO (creating missing parts), and delete archives
with a reason + refund instead of hard-deleting.
"""
import pytest
from sqlalchemy import select

from backend.models import Account, Payment, PurchaseOrder, Supplier


async def _me(client, H):
    return (await client.get("/api/auth/me", headers=H)).json()


async def _loc_id(client, H):
    d = (await client.get("/api/staff/locations", headers=H)).json()
    items = d if isinstance(d, list) else d.get("items", d)
    return items[0]["id"]


async def _client_id(client, H):
    r = await client.post("/api/clients", json={"name": "Owner", "phone": "+10000000001"}, headers=H)
    return r.json()["id"]


async def _make_order(client, H, lines=None):
    lid = await _loc_id(client, H)
    cid = await _client_id(client, H)
    body = {"client_id": cid, "location_id": lid, "complaint": "screen cracked", "lines": lines or []}
    r = await client.post("/api/orders", json=body, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── duplicate ────────────────────────────────────────────────────────────────
async def test_duplicate_copies_lines_as_new_draft(client, tenant):
    oid = await _make_order(client, tenant, lines=[
        {"line_type": "labour", "kind": "service", "description": "Diagnostics", "qty": 1, "unit_price": 500},
        {"line_type": "part", "kind": "product", "description": "Screen", "qty": 2, "unit_price": 1000, "cost_price": 600},
    ])
    r = await client.post(f"/api/orders/{oid}/duplicate", json={}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["line_count"] == 2
    new_id = r.json()["id"]
    assert new_id != oid
    dup = (await client.get(f"/api/orders/{new_id}", headers=tenant)).json()
    dup = dup.get("order", dup)
    assert len(dup["lines"]) == 2
    assert dup["status"] == "new"                 # fresh draft
    # both appear in the list (duplicate is a real, separate order)
    ids = [o["id"] for o in (await client.get("/api/orders?limit=100", headers=tenant)).json()["items"]]
    assert oid in ids and new_id in ids


async def test_duplicate_can_schedule_the_copy(client, tenant):
    """Duplicate accepts an optional schedule (Schedule from / Schedule to) and
    stamps it onto the new copy so it lands on the calendar immediately."""
    oid = await _make_order(client, tenant)
    body = {"scheduled_for": "2026-08-01T09:00", "scheduled_to": "2026-08-01T11:30"}
    r = await client.post(f"/api/orders/{oid}/duplicate", json=body, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["scheduled_for"].startswith("2026-08-01T09:00")
    new_id = r.json()["id"]
    dup = (await client.get(f"/api/orders/{new_id}", headers=tenant)).json()
    dup = dup.get("order", dup)
    assert dup["scheduled_for"].startswith("2026-08-01T09:00")
    assert dup["scheduled_to"].startswith("2026-08-01T11:30")


async def test_duplicate_without_schedule_is_unscheduled(client, tenant):
    """Omitting the schedule keeps the historical behaviour: a fresh, unscheduled draft."""
    oid = await _make_order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/duplicate", json={}, headers=tenant)
    assert r.status_code == 201, r.text
    assert r.json()["scheduled_for"] is None
    dup = (await client.get(f"/api/orders/{r.json()['id']}", headers=tenant)).json()
    dup = dup.get("order", dup)
    assert dup["scheduled_for"] is None


async def test_duplicate_rejects_reversed_schedule(client, tenant):
    oid = await _make_order(client, tenant)
    body = {"scheduled_for": "2026-08-01T11:30", "scheduled_to": "2026-08-01T09:00"}
    r = await client.post(f"/api/orders/{oid}/duplicate", json=body, headers=tenant)
    assert r.status_code == 400, r.text


# ── create PO ────────────────────────────────────────────────────────────────
async def _supplier(db, org_id):
    s = Supplier(org_id=org_id, name="Acme Parts", email="sales@acme.test")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s.id


async def test_create_po_from_order(client, tenant, db):
    me = await _me(client, tenant)
    sid = await _supplier(db, me["org_id"])
    oid = await _make_order(client, tenant, lines=[
        {"line_type": "part", "kind": "product", "description": "Rare Screen", "qty": 5, "unit_price": 1000, "cost_price": 600},
    ])
    r = await client.post(f"/api/orders/{oid}/create-po", json={"supplier_id": sid, "auto_send": True}, headers=tenant)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["line_count"] == 1 and j["status"] == "ordered" and j["sent"] is True
    assert len(j["created_parts"]) == 1          # the custom product became a real part
    # PO is linked back to the work order
    po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == j["po_id"]))).scalar_one()
    assert po.source_order_id == oid


async def test_create_po_nothing_to_order(client, tenant, db):
    me = await _me(client, tenant)
    sid = await _supplier(db, me["org_id"])
    oid = await _make_order(client, tenant, lines=[
        {"line_type": "labour", "kind": "service", "description": "Labour only", "qty": 1, "unit_price": 300},
    ])
    r = await client.post(f"/api/orders/{oid}/create-po", json={"supplier_id": sid}, headers=tenant)
    assert r.status_code == 400        # no parts need ordering


# ── archive / delete ─────────────────────────────────────────────────────────
async def test_archive_requires_reason(client, tenant):
    oid = await _make_order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "  ", "refund": False}, headers=tenant)
    assert r.status_code == 422


async def test_archive_hides_and_records_reason(client, tenant):
    oid = await _make_order(client, tenant)
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "Customer cancelled", "refund": False}, headers=tenant)
    assert r.status_code == 200 and r.json()["archived"] is True
    # gone from the normal list
    ids = [o["id"] for o in (await client.get("/api/orders?limit=100", headers=tenant)).json()["items"]]
    assert oid not in ids
    # present in the archive with reason + who
    arch = (await client.get("/api/orders/archived", headers=tenant)).json()["items"]
    row = next(x for x in arch if x["id"] == oid)
    assert row["archive_reason"] == "Customer cancelled"
    assert row["archived_by"]                    # attributed to the actor
    # still openable directly (not destroyed)
    assert (await client.get(f"/api/orders/{oid}", headers=tenant)).status_code == 200


async def test_delete_soft_archives(client, tenant):
    oid = await _make_order(client, tenant)
    r = await client.request("DELETE", f"/api/orders/{oid}?reason=Created%20in%20error&refund=false", headers=tenant)
    assert r.status_code == 200
    arch = (await client.get("/api/orders/archived", headers=tenant)).json()["items"]
    assert any(x["id"] == oid for x in arch)     # archived, not hard-deleted


async def test_archive_refunds_payments(client, tenant, db):
    me = await _me(client, tenant)
    oid = await _make_order(client, tenant)
    # record a paid account + a payment against the order
    acc = Account(org_id=me["org_id"], name="Cash", active=True)
    db.add(acc)
    await db.flush()
    db.add(Payment(order_id=oid, method="cash", kind="payment", amount=1500.0, account_id=acc.id))
    await db.commit()
    r = await client.post(f"/api/orders/{oid}/archive", json={"reason": "Refund & remove", "refund": True}, headers=tenant)
    assert r.status_code == 200
    assert r.json()["refunded"] == 1500.0
    # a refund payment was written
    refunds = (await db.execute(select(Payment).where(
        Payment.order_id == oid, Payment.kind == "refund"))).scalars().all()
    assert len(refunds) == 1 and refunds[0].amount == 1500.0
