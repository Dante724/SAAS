"""Money: documents stay consistent with stock, tills and each other (security phase 3).

Regressions for audit findings B6, B8, B10, B13:
- archive -> restore -> archive returned an order's parts to stock twice;
- an order with two invoices made payment/payment links 500 (MultipleResultsFound);
- cash taken on a work order never counted toward the drawer close, hiding shortages;
- voiding or correcting just the cash row of a payment desynced the documents.
"""
import pytest
from sqlalchemy import func, select

from backend.models import Invoice, StockLevel, Transaction
from backend.tests.test_money_hardening import _account, _order_with_total


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


async def _stock(db, part_id):
    db.expire_all()
    return float((await db.execute(select(func.coalesce(func.sum(StockLevel.qty_available), 0))
                                   .where(StockLevel.part_id == part_id))).scalar() or 0)


@pytest.mark.asyncio
async def test_archive_restore_archive_returns_parts_once(client, tenant, db):
    loc = await _loc(client, tenant)
    p = (await client.post("/api/inventory/parts", json={"name": "Screen", "sku": "S1"}, headers=tenant)).json()
    await client.post(f"/api/inventory/parts/{p['id']}/adjust", headers=tenant,
                      json={"location_id": loc, "qty": 10, "movement_type": "receive"})
    cid = (await client.post("/api/clients", json={"name": "C"}, headers=tenant)).json()["id"]
    oid = (await client.post("/api/orders", json={"client_id": cid, "location_id": loc}, headers=tenant)).json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", headers=tenant, json={
        "line_type": "part", "kind": "product", "ref_id": p["id"], "description": "Screen", "qty": 2, "unit_price": 50})
    assert r.status_code in (200, 201), r.text
    assert await _stock(db, p["id"]) == 8
    assert (await client.delete(f"/api/orders/{oid}?refund=false", headers=tenant)).status_code in (200, 201)
    assert await _stock(db, p["id"]) == 10
    assert (await client.post(f"/api/orders/{oid}/restore", headers=tenant)).status_code == 200
    assert (await client.delete(f"/api/orders/{oid}?refund=false", headers=tenant)).status_code in (200, 201)
    assert await _stock(db, p["id"]) == 10            # not 12


@pytest.mark.asyncio
async def test_order_with_two_invoices_can_still_be_paid(client, tenant, db):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    for _ in range(2):
        r = await client.post("/api/finance/invoices", headers=tenant, json={"order_id": oid})
        assert r.status_code in (200, 201), r.text
    assert (await db.execute(select(func.count()).select_from(Invoice).where(Invoice.order_id == oid))).scalar() >= 2
    r = await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant,
                          json={"method": "cash", "account_id": aid, "amount": 100})
    assert r.status_code in (200, 201), r.text
    link = await client.post(f"/api/orders/{oid}/payment-link", headers=tenant, json={})
    assert link.status_code in (400, 201), link.text      # 400 = settled; never a 500


@pytest.mark.asyncio
async def test_cash_order_payment_counts_toward_drawer_close(client, tenant):
    loc = await _loc(client, tenant)
    await client.post("/api/cash/accounts", headers=tenant,
                      json={"name": "Front till", "payment_option": "cash", "location_id": loc})
    aid = next(a["id"] for a in (await client.get("/api/cash/accounts", headers=tenant)).json()["items"]
               if a["name"] == "Front till")
    sid = (await client.post("/api/finance/shifts/open", headers=tenant,
                             json={"location_id": loc, "opening_float": 50})).json()["id"]
    _cid, oid = await _order_with_total(client, tenant, 100)
    await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant,
                      json={"method": "cash", "account_id": aid, "amount": 100})
    # drawer actually holds 50 + 100 - 20 missing = 130 → 20 short
    r = await client.post(f"/api/finance/shifts/{sid}/close?closing_float=130", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.json()["discrepancy"] == -20.0, r.json()


@pytest.mark.asyncio
async def test_payment_cash_leg_cannot_be_voided_or_corrected_alone(client, tenant, db):
    _cid, oid = await _order_with_total(client, tenant, 100)
    aid = await _account(client, tenant)
    await client.post(f"/api/cash/orders/{oid}/pay", headers=tenant,
                      json={"method": "cash", "account_id": aid, "amount": 100})
    txn = (await db.execute(select(Transaction).where(Transaction.ref_id == oid))).scalars().first()
    assert txn is not None
    assert (await client.delete(f"/api/cash/transactions/{txn.id}", headers=tenant)).status_code == 409
    r = await client.post(f"/api/cash/transactions/{txn.id}/correct", headers=tenant,
                          json={"delta": -100, "reason": "oops"})
    assert r.status_code == 409
