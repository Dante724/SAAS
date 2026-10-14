"""Build 144am — the PDF print endpoints must return a real PDF (200) with auth,
so the front-end's authed openPrintPdf() opens a document instead of a 401 blank tab."""
import pytest

from backend.models import Supplier


async def _loc(client, h):
    return (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_purchase_order_pdf_prints(db, client, tenant):
    org_id = (await client.get("/api/auth/me", headers=tenant)).json()["org_id"]
    s = Supplier(org_id=org_id, name="Acme", balance=0.0)
    db.add(s)
    await db.commit()
    loc = await _loc(client, tenant)
    p1 = (await client.post("/api/inventory/parts", json={"name": "Screen", "cost_price": 10}, headers=tenant)).json()["id"]
    pid = (await client.post("/api/inventory/po", json={
        "supplier_id": s.id, "location_id": loc,
        "lines": [{"part_id": p1, "qty_ordered": 2, "unit_cost": 10}]}, headers=tenant)).json()["po_id"]
    # add a charge + supplier invoice so the enriched print path is exercised
    await client.patch(f"/api/inventory/po/{pid}", json={
        "supplier_invoice_no": "INV-9", "charges": [{"label": "Shipping", "amount": 5}]}, headers=tenant)

    r = await client.get(f"/api/print/purchase-order/{pid}", headers=tenant)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_print_requires_auth(client):
    # without the bearer token the endpoint must not silently succeed
    r = await client.get("/api/print/purchase-order/whatever")
    assert r.status_code in (401, 403)
