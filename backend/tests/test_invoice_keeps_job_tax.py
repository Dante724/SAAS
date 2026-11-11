"""Hardening council: an invoice created from the Invoices screen for a taxed job carried tax 0.

POST /api/finance/invoices passed `discount_pct=… or 0, tax_pct=… or 0` into new_invoice, whose
"percentages given" branch then set the tax to 0 and skipped the whole-jobs branch that keeps
the tax the jobs already carry — so a 100 @ 10% imposed job billed as 110 with tax 0 (and the
e-invoice export showed 0 tax). Percentages are only percentages when the caller sent them.
"""
import pytest

LINE = {"line_type": "labour", "kind": "custom", "description": "Work", "unit_price": 100, "qty": 1,
        "tax_rate": 10, "tax_mode": "imposed"}


async def _job(client, h):
    loc = (await client.get("/api/staff/locations", headers=h)).json()["items"][0]["id"]
    cid = (await client.post("/api/clients", json={"name": "Cust"}, headers=h)).json()["id"]
    r = await client.post("/api/orders", json={"client_id": cid, "location_id": loc, "complaint": "x",
                                               "is_estimate": False, "lines": []}, headers=h)
    assert r.status_code in (200, 201), r.text
    oid = r.json()["id"]
    r = await client.post(f"/api/orders/{oid}/lines", json=LINE, headers=h)
    assert r.status_code in (200, 201), r.text
    job = (await client.get(f"/api/orders/{oid}", headers=h)).json()
    assert job["total"] == pytest.approx(110), job     # 100 + 10% sales tax on top
    return oid, cid


@pytest.mark.asyncio
async def test_create_invoice_keeps_job_tax(client, tenant):
    oid, cid = await _job(client, tenant)
    r = await client.post("/api/finance/invoices", json={"order_id": oid, "client_id": cid}, headers=tenant)
    assert r.status_code == 201, r.text
    inv = (await client.get(f"/api/finance/invoices/{r.json()['id']}", headers=tenant)).json()
    assert inv["total"] == pytest.approx(110)
    assert inv["tax"] == pytest.approx(10), inv
    assert inv["subtotal"] == pytest.approx(100), inv


@pytest.mark.asyncio
async def test_create_invoice_explicit_percentages_still_apply(client, tenant):
    """A caller that does send percentages gets them (the manual-invoice path is unchanged)."""
    r = await client.post("/api/finance/invoices", json={
        "lines": [{"description": "Repair", "qty": 1, "unit_price": 100}],
        "discount_pct": 10, "tax_pct": 20}, headers=tenant)
    assert r.status_code == 201, r.text
    inv = (await client.get(f"/api/finance/invoices/{r.json()['id']}", headers=tenant)).json()
    assert inv["subtotal"] == pytest.approx(100)
    assert inv["discount"] == pytest.approx(10)
    assert inv["tax"] == pytest.approx(18)
    assert inv["total"] == pytest.approx(108)
