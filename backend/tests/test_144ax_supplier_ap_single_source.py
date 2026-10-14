"""Build 144ax — Supplier.balance (running column) and the linked contact ledger must stay the
same debt seen two ways: they now move through one write-path (ledger.adjust_supplier_ap) so they
can never drift. Covers the reconciliation invariant and the cash-return-delete bug where deleting
a cash-settled return wrongly inflated the column (it never touched it on create)."""
import pytest
from sqlalchemy import select


async def _me(client, h):
    return (await client.get("/api/auth/me", headers=h)).json()


async def _linked_supplier(client, h, db):
    """A supplier mirrored by a real contact, so both balances exist and must agree."""
    from backend.models import Client as _Client
    from backend.models import Supplier
    me = await _me(client, h)
    c = _Client(org_id=me["org_id"], name="Acme Vendor", is_supplier=True)
    db.add(c); await db.flush()
    sup = Supplier(org_id=me["org_id"], client_id=c.id, name="Acme Vendor", balance=0)
    db.add(sup); await db.commit(); await db.refresh(sup)
    return sup, c.id


async def _part_with_stock(client, h, db, qty=10):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    p = (await client.post("/api/inventory/parts", json={"name": "Board", "sku": "BRD"}, headers=h)).json()
    await client.post(f"/api/inventory/parts/{p['id']}/adjust",
                      json={"location_id": loc, "qty": qty, "movement_type": "receive"}, headers=h)
    w = (await client.get("/api/settings/warehouses?kind=product", headers=h)).json()["items"][0]
    return p, w


async def _discrepancy(client, h, supplier_id):
    led = (await client.get(f"/api/suppliers/{supplier_id}/ledger", headers=h)).json()
    return led["discrepancy"], led["reconciled"]


@pytest.mark.asyncio
async def test_balance_return_keeps_column_and_ledger_reconciled(client, tenant, db):
    from backend.models import Supplier
    sup, cid = await _linked_supplier(client, tenant, db)
    p, w = await _part_with_stock(client, tenant, db, qty=10)

    r = await client.post("/api/stock-docs/returns", json={
        "warehouse_id": w["id"], "supplier_id": sup.id, "settlement": "balance",
        "lines": [{"part_id": p["id"], "qty": 2, "unit_cost": 5}]}, headers=tenant)
    assert r.status_code == 201, r.text

    sup2 = (await db.execute(select(Supplier).where(Supplier.id == sup.id))).scalar_one()
    await db.refresh(sup2)
    assert sup2.balance == pytest.approx(-10.0)        # a return means we owe them 10 less
    disc, reconciled = await _discrepancy(client, tenant, sup.id)
    assert reconciled and disc == 0.0, (disc, sup2.balance)


@pytest.mark.asyncio
async def test_balance_return_roundtrip_reconciles_to_zero(client, tenant, db):
    """Create then delete a balance-settled return: the AP column and the contact ledger must
    both return to zero and stay reconciled through every step (single write-path)."""
    from backend.models import Supplier
    sup, cid = await _linked_supplier(client, tenant, db)
    p, w = await _part_with_stock(client, tenant, db, qty=10)

    r = await client.post("/api/stock-docs/returns", json={
        "warehouse_id": w["id"], "supplier_id": sup.id, "settlement": "balance",
        "lines": [{"part_id": p["id"], "qty": 2, "unit_cost": 5}]}, headers=tenant)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    disc, reconciled = await _discrepancy(client, tenant, sup.id)
    assert reconciled and disc == 0.0

    d = await client.delete(f"/api/stock-docs/returns/{rid}", headers=tenant)
    assert d.status_code == 200, d.text
    sup2 = (await db.execute(select(Supplier).where(Supplier.id == sup.id))).scalar_one()
    await db.refresh(sup2)
    assert sup2.balance == pytest.approx(0.0), sup2.balance      # back to where we started
    disc, reconciled = await _discrepancy(client, tenant, sup.id)
    assert reconciled and disc == 0.0, (disc, sup2.balance)
