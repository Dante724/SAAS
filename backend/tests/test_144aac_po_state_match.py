"""144aac — PO state machine + three-way match (order vs received vs invoice)."""
import pytest
from sqlalchemy import select

from backend.models import Location, Part, PurchaseOrder, Supplier


async def _setup(client, tenant, db):
    me = (await client.get("/api/auth/me", headers=tenant)).json()
    loc = (await client.get("/api/staff/locations", headers=tenant)).json()["items"][0]["id"]
    pid = (await client.post("/api/inventory/parts", json={"name": "Belt", "sku": "BELT-1"}, headers=tenant)).json()["id"]
    sup = Supplier(org_id=me["org_id"], name="S", balance=0)
    db.add(sup)
    await db.flush()
    po = PurchaseOrder(supplier_id=sup.id, location_id=loc, po_number="PO-M1", status="ordered", on_credit=True)
    db.add(po)
    await db.flush()
    from backend.models import POLine
    db.add(POLine(po_id=po.id, part_id=pid, qty_ordered=10, unit_cost=5))
    await db.commit()
    return po.id


@pytest.mark.asyncio
async def test_three_way_match_states(client, tenant, db):
    po_id = await _setup(client, tenant, db)

    # pending: nothing received yet
    m = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["match"]
    assert m["status"] == "pending"

    # receive all 10 @ 5 → expected cost 50, no invoice yet → unbilled
    await client.post(f"/api/inventory/po/{po_id}/receive", json={}, headers=tenant)
    m = (await client.get(f"/api/inventory/po/{po_id}", headers=tenant)).json()["match"]
    assert m["status"] == "unbilled" and m["received_qty"] == 10 and m["expected_cost"] == 50.0

    # supplier bills exactly 50 → matched
    r = await client.post(f"/api/inventory/po/{po_id}/invoice", json={"supplier_invoice_total": 50}, headers=tenant)
    assert r.json()["match"]["status"] == "matched"

    # supplier bills 60 → price variance flagged
    r = await client.post(f"/api/inventory/po/{po_id}/invoice", json={"supplier_invoice_total": 60}, headers=tenant)
    m = r.json()["match"]
    assert m["status"] == "variance" and m["price_variance"] == 10.0


@pytest.mark.asyncio
async def test_cannot_cancel_or_receive_received_po(client, tenant, db):
    po_id = await _setup(client, tenant, db)
    await client.post(f"/api/inventory/po/{po_id}/receive", json={}, headers=tenant)  # → received

    # received is terminal: cancel blocked
    c = await client.post(f"/api/inventory/po/{po_id}/cancel", headers=tenant)
    assert c.status_code == 409, c.text
    # …and you can't receive it again
    r = await client.post(f"/api/inventory/po/{po_id}/receive", json={}, headers=tenant)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_cancel_open_po(client, tenant, db):
    po_id = await _setup(client, tenant, db)
    c = await client.post(f"/api/inventory/po/{po_id}/cancel", headers=tenant)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))).scalar_one()
    assert po.status == "cancelled"
